"""
Anomaly Detector — polls Prometheus, detects anomalies, fires alerts
=====================================================================
Runs as a background thread inside the AIOps pipeline.
Queries Prometheus every POLL_INTERVAL seconds.
Compares metrics against baseline thresholds.
Stores alerts in thread-safe in-memory list.
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger('aiops.detector')

PROMETHEUS_URL = os.environ.get('PROMETHEUS_URL', 'http://prometheus:9090')
POLL_INTERVAL = int(os.environ.get('DETECTOR_POLL_INTERVAL', '10'))

# Default anomaly thresholds (overridden by baseline if available)
DEFAULT_THRESHOLDS = {
    'latency_p99_warn': 0.2,       # 200ms
    'latency_p99_crit': 0.5,       # 500ms
    'error_rate_warn': 0.05,       # 5%
    'error_rate_crit': 0.10,       # 10%
    'downstream_error_rate_crit': 0.02, # absolute errors/sec threshold (since traffic-gen is ~0.1 req/s)
    'cpu_saturation_crit': 0.80,   # 80%
}

# Services to monitor
MONITORED_SERVICES = [
    'frontend', 'api-gateway', 'payment-svc', 'inventory-svc',
    'notification-svc', 'checkout-svc', 'auth-svc',
    'log-collector', 'dns-resolver', 'cache-svc',
    'payment-db', 'inventory-db', 'kafka',
]


class AnomalyDetector:
    """
    Background anomaly detector.
    Polls Prometheus, compares to thresholds, creates alerts.
    """

    def __init__(self, prometheus_url: str = PROMETHEUS_URL):
        self.prom_url = prometheus_url
        self.alerts: list[dict] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.baseline: dict = {}
        self.thresholds = DEFAULT_THRESHOLDS.copy()
        self._alert_counter = 0

    def start(self):
        """Start the background detection loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f'Detector started — polling {self.prom_url} every {POLL_INTERVAL}s')

    def stop(self):
        self._running = False

    def set_baseline(self, baseline: dict):
        """Set baseline metrics from capture_baseline.py output."""
        self.baseline = baseline
        logger.info(f'Baseline set with {len(baseline)} service entries')

    def get_alerts(self, since: float = 0) -> list[dict]:
        """Return alerts fired since Unix timestamp `since`."""
        with self._lock:
            if since <= 0:
                return list(self.alerts)
            return [a for a in self.alerts if a.get('_ts_unix', 0) >= since]

    def clear_alerts(self):
        with self._lock:
            self.alerts.clear()

    # ─── Internal ────────────────────────────────────────────────

    def _loop(self):
        # Wait for Prometheus to be ready
        time.sleep(15)
        while self._running:
            try:
                self._detect_cycle()
            except Exception as exc:
                logger.error(f'Detector cycle failed: {exc}')
            time.sleep(POLL_INTERVAL)

    def _detect_cycle(self):
        """One detection cycle — query Prom + check thresholds."""
        now = datetime.now(timezone.utc)
        now_unix = now.timestamp()

        # 1. Latency anomaly: p99 per service
        latency_data = self._prom_query(
            'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[1m])) by (le, service))'
        )
        for item in latency_data:
            service = item['metric'].get('service', '')
            value = float(item['value'][1])
            if service not in MONITORED_SERVICES:
                continue
            if value > self.thresholds['latency_p99_crit']:
                self._fire_alert(now, service, 'http_request_latency_p99',
                                 'crit', value, self.thresholds['latency_p99_crit'])
            elif value > self.thresholds['latency_p99_warn']:
                self._fire_alert(now, service, 'http_request_latency_p99',
                                 'warn', value, self.thresholds['latency_p99_warn'])

        # 2. Error rate anomaly (ratio)
        error_data = self._prom_query(
            'sum(rate(http_errors_total[1m])) by (service) / sum(rate(http_requests_total[1m])) by (service)'
        )
        for item in error_data:
            service = item['metric'].get('service', '')
            value = float(item['value'][1])
            if service not in MONITORED_SERVICES:
                continue
            if value > self.thresholds['error_rate_crit']:
                self._fire_alert(now, service, 'http_error_rate',
                                 'crit', value, self.thresholds['error_rate_crit'])
            elif value > self.thresholds['error_rate_warn']:
                self._fire_alert(now, service, 'http_error_rate',
                                 'warn', value, self.thresholds['error_rate_warn'])

        # 3. Downstream call errors (absolute rate grouped by downstream target)
        ds_error_data = self._prom_query(
            'sum(rate(downstream_call_errors_total[1m])) by (downstream)'
        )
        for item in ds_error_data:
            downstream = item['metric'].get('downstream', '')
            if downstream == 'kafka-mock':
                downstream = 'kafka'
            value = float(item['value'][1])
            if downstream not in MONITORED_SERVICES:
                continue
            if value > self.thresholds['downstream_error_rate_crit']:
                self._fire_alert(now, downstream, 'downstream_error_rate',
                                 'crit', value, self.thresholds['downstream_error_rate_crit'])

        # 4. Downstream latency anomaly (p99 grouped by downstream target)
        ds_latency_data = self._prom_query(
            'histogram_quantile(0.99, sum(rate(downstream_call_duration_seconds_bucket[1m])) by (le, downstream))'
        )
        for item in ds_latency_data:
            downstream = item['metric'].get('downstream', '')
            if downstream == 'kafka-mock':
                downstream = 'kafka'
            value = float(item['value'][1])
            if downstream not in MONITORED_SERVICES:
                continue
            if value > 1.0:  # > 1s downstream call
                self._fire_alert(now, downstream, 'downstream_latency_p99',
                                 'crit', value, 1.0)
            elif value > 0.5:  # > 500ms
                self._fire_alert(now, downstream, 'downstream_latency_p99',
                                 'warn', value, 0.5)

        # 5. Service availability — check if scrape target is up
        up_data = self._prom_query('up{job="mock-services"}')
        for item in up_data:
            instance = item['metric'].get('instance', '')
            value = float(item['value'][1])
            service = instance.split(':')[0] if ':' in instance else instance
            if service not in MONITORED_SERVICES:
                continue
            if value < 1:
                self._fire_alert(now, service, 'service_availability',
                                 'crit', 0, 1)

    def _fire_alert(self, ts: datetime, service: str, metric: str,
                    severity: str, value: float, threshold: float):
        """Create and store an alert — with dedup window of 30s."""
        with self._lock:
            # Dedup: don't fire same (service, metric) within 30s
            ts_unix = ts.timestamp()
            for existing in reversed(self.alerts[-50:]):
                if (existing['service'] == service
                        and existing['metric'] == metric
                        and ts_unix - existing.get('_ts_unix', 0) < 30):
                    return  # Already fired recently

            self._alert_counter += 1
            alert = {
                'id': f'alert-{self._alert_counter:04d}',
                'ts': ts.isoformat(),
                '_ts_unix': ts_unix,
                'service': service,
                'metric': metric,
                'severity': severity,
                'value': round(value, 4),
                'threshold': round(threshold, 4),
                'labels': {'instance': f'{service}:8080'},
            }
            self.alerts.append(alert)
            logger.info(
                f'ALERT [{severity}] {service}/{metric} = {value:.4f} '
                f'(threshold={threshold:.4f})'
            )

    def _prom_query(self, query: str) -> list:
        """Execute instant PromQL query, return result list."""
        try:
            r = requests.get(
                f'{self.prom_url}/api/v1/query',
                params={'query': query},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            if data.get('status') == 'success':
                return data['data']['result']
            return []
        except Exception as exc:
            logger.debug(f'PromQL query failed: {exc}')
            return []
