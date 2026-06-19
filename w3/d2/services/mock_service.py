"""
Generic Mock Microservice for AIOps Chaos Engineering Lab W3-D2
================================================================
Single codebase for all 10 services. Behavior configured via env vars.

ENV vars:
  SERVICE_NAME     - Service identifier (e.g., "payment-svc")
  SERVICE_PORT     - Listen port (default: 8080)
  DOWNSTREAM_URLS  - Comma-separated downstream health URLs
  BASE_LATENCY_MS  - Base processing latency in ms (default: 20)
  BASE_ERROR_RATE  - Base error rate 0-1 (default: 0.005)
"""

import os
import sys
import time
import random
import threading
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request, Response
from prometheus_client import (
    Counter, Histogram, Gauge, Summary,
    generate_latest, CONTENT_TYPE_LATEST,
)
import requests as http_client

# ── Config from environment ──────────────────────────────────────────
SERVICE_NAME = os.environ.get('SERVICE_NAME', 'unknown-svc')
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '8080'))
DOWNSTREAM_URLS = [
    u.strip()
    for u in os.environ.get('DOWNSTREAM_URLS', '').split(',')
    if u.strip()
]
BASE_LATENCY_MS = int(os.environ.get('BASE_LATENCY_MS', '20'))
BASE_ERROR_RATE = float(os.environ.get('BASE_ERROR_RATE', '0.005'))

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format=f'%(asctime)s [{SERVICE_NAME}] %(levelname)s %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger(SERVICE_NAME)

# ── Flask app ────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Prometheus metrics ───────────────────────────────────────────────
REQUEST_TOTAL = Counter(
    'http_requests_total', 'Total HTTP requests',
    ['service', 'method', 'endpoint', 'status_code'],
)
REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds', 'HTTP request latency',
    ['service', 'endpoint'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
ERROR_TOTAL = Counter(
    'http_errors_total', 'Total HTTP errors',
    ['service', 'error_type'],
)
DOWNSTREAM_LATENCY = Histogram(
    'downstream_call_duration_seconds', 'Downstream call latency',
    ['service', 'downstream'],
    buckets=[0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
DOWNSTREAM_ERRORS = Counter(
    'downstream_call_errors_total', 'Downstream call errors',
    ['service', 'downstream', 'error_type'],
)
SERVICE_UP = Gauge('service_up', 'Service health', ['service'])
SERVICE_UP.labels(service=SERVICE_NAME).set(1)

# -- Injectable fault parameters (runtime-mutable) --
_inject_error_rate = float(os.environ.get('INJECT_ERROR_RATE', '0'))
_inject_latency_ms = float(os.environ.get('INJECT_LATENCY_MS', '0'))
_inject_lock = threading.Lock()


# =====================================================================
# Routes
# =====================================================================

@app.route('/health')
def health():
    """Liveness / readiness probe."""
    return jsonify({
        'status': 'ok',
        'service': SERVICE_NAME,
        'ts': datetime.now(timezone.utc).isoformat(),
    })


@app.route('/checkout/health')
def checkout_health():
    """External probe endpoint (§6.4) — checks full downstream chain."""
    errors = []
    for url in DOWNSTREAM_URLS:
        try:
            r = http_client.get(url, timeout=2)
            if r.status_code >= 400:
                errors.append(f'{url}: HTTP {r.status_code}')
        except Exception as exc:
            errors.append(f'{url}: {type(exc).__name__}')
    if errors:
        return jsonify({'status': 'degraded', 'errors': errors}), 503
    return jsonify({'status': 'ok', 'service': SERVICE_NAME})


@app.route('/metrics')
def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route('/config', methods=['GET', 'POST'])
def config_endpoint():
    """Runtime config -- used for fault injection via HTTP."""
    global _inject_error_rate, _inject_latency_ms
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        with _inject_lock:
            if 'error_rate' in data:
                _inject_error_rate = float(data['error_rate'])
                logger.info(f'Inject error_rate set to {_inject_error_rate}')
            if 'latency_ms' in data:
                _inject_latency_ms = float(data['latency_ms'])
                logger.info(f'Inject latency_ms set to {_inject_latency_ms}')
    return jsonify({
        'service': SERVICE_NAME,
        'inject_error_rate': _inject_error_rate,
        'inject_latency_ms': _inject_latency_ms,
        'base_error_rate': BASE_ERROR_RATE,
    })


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def handle_request(path):
    """
    Generic request handler — simulates business logic.
    1. Check injectable error rate
    2. Simulate processing latency
    3. Call downstream services
    4. Return response with timing
    """
    start = time.time()
    endpoint = f'/{path}' if path else '/'

    # -- Injected errors --
    with _inject_lock:
        effective_error_rate = BASE_ERROR_RATE + _inject_error_rate
        extra_latency_ms = _inject_latency_ms
    if random.random() < effective_error_rate:
        latency = time.time() - start
        REQUEST_TOTAL.labels(
            service=SERVICE_NAME, method=request.method,
            endpoint=endpoint, status_code='500',
        ).inc()
        REQUEST_LATENCY.labels(service=SERVICE_NAME, endpoint=endpoint).observe(latency)
        ERROR_TOTAL.labels(service=SERVICE_NAME, error_type='internal_error').inc()
        return jsonify({'error': 'Internal Server Error', 'service': SERVICE_NAME}), 500

    # -- Simulate processing (base + injected latency) --
    jitter = random.gauss(0, BASE_LATENCY_MS * 0.15)
    sleep_ms = max(1, BASE_LATENCY_MS + jitter + extra_latency_ms)
    time.sleep(sleep_ms / 1000.0)

    # ── Call downstream services ──
    downstream_results = []
    for url in DOWNSTREAM_URLS:
        ds_name = _extract_host(url)
        ds_start = time.time()
        try:
            r = http_client.get(url, timeout=5)
            ds_lat = time.time() - ds_start
            DOWNSTREAM_LATENCY.labels(service=SERVICE_NAME, downstream=ds_name).observe(ds_lat)
            downstream_results.append({
                'target': ds_name, 'status': r.status_code,
                'latency_ms': round(ds_lat * 1000, 2),
            })
        except http_client.exceptions.Timeout:
            ds_lat = time.time() - ds_start
            DOWNSTREAM_LATENCY.labels(service=SERVICE_NAME, downstream=ds_name).observe(ds_lat)
            DOWNSTREAM_ERRORS.labels(
                service=SERVICE_NAME, downstream=ds_name, error_type='timeout',
            ).inc()
            ERROR_TOTAL.labels(service=SERVICE_NAME, error_type='downstream_timeout').inc()
            downstream_results.append({
                'target': ds_name, 'status': 'timeout',
                'latency_ms': round(ds_lat * 1000, 2),
            })
        except Exception as exc:
            ds_lat = time.time() - ds_start
            DOWNSTREAM_ERRORS.labels(
                service=SERVICE_NAME, downstream=ds_name, error_type='connection_error',
            ).inc()
            ERROR_TOTAL.labels(service=SERVICE_NAME, error_type='downstream_error').inc()
            downstream_results.append({
                'target': ds_name, 'status': 'error',
                'error': type(exc).__name__,
            })

    total_latency = time.time() - start
    REQUEST_TOTAL.labels(
        service=SERVICE_NAME, method=request.method,
        endpoint=endpoint, status_code='200',
    ).inc()
    REQUEST_LATENCY.labels(service=SERVICE_NAME, endpoint=endpoint).observe(total_latency)

    return jsonify({
        'service': SERVICE_NAME,
        'path': endpoint,
        'latency_ms': round(total_latency * 1000, 2),
        'downstream': downstream_results,
    })


# =====================================================================
# Helpers
# =====================================================================

def _extract_host(url: str) -> str:
    """Extract hostname from URL for metric labeling."""
    try:
        after_scheme = url.split('//')[1] if '//' in url else url
        return after_scheme.split(':')[0].split('/')[0]
    except Exception:
        return url


# =====================================================================
# Main
# =====================================================================

if __name__ == '__main__':
    logger.info(f'Starting {SERVICE_NAME} on :{SERVICE_PORT}')
    logger.info(f'Downstream: {DOWNSTREAM_URLS}')
    logger.info(f'Base latency: {BASE_LATENCY_MS}ms, error rate: {BASE_ERROR_RATE}')
    app.run(host='0.0.0.0', port=SERVICE_PORT, threaded=True)
