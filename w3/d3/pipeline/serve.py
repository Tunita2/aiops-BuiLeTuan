"""
AIOps Pipeline — FastAPI Serving Layer for Chaos Engineering Lab W3-D2
=======================================================================
Endpoints:
  GET  /healthz                 — Liveness check
  GET  /alerts?since=<ts>       — List alerts fired since timestamp
  POST /correlate               — Cluster recent alerts
  POST /rca                     — Root cause analysis
  GET  /metrics                 — Prometheus metrics
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Optional

# ==============================================================================
# Logging
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [aiops] %(levelname)s %(name)s — %(message)s',
)
logger = logging.getLogger('aiops.serve')

# ==============================================================================
# FastAPI App
# ==============================================================================

APP_VERSION = '2.0.0-chaos'

app = FastAPI(
    title='AIOps Chaos Engineering Pipeline',
    version=APP_VERSION,
    description='Detect anomalies → Correlate alerts → RCA. '
                'Built for W3-D2 Chaos Engineering Lab.',
)

# ==============================================================================
# Load Service Graph + Incident History
# ==============================================================================

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = BASE_DIR / 'dataset'

import networkx as nx

def _build_graph(services_data: dict) -> nx.DiGraph:
    G = nx.DiGraph()
    for svc in services_data.get('services', []):
        G.add_node(svc['name'], **svc)
    for store in services_data.get('stores', []):
        G.add_node(store['name'], **store)
    for edge in services_data.get('edges', []):
        G.add_edge(edge['from'], edge['to'], type=edge.get('type', 'http'))
    return G

with open(DATASET_DIR / 'services.json', 'r', encoding='utf-8') as f:
    _svc_data = json.load(f)
GRAPH = _build_graph(_svc_data)
logger.info(f'Graph loaded: {GRAPH.number_of_nodes()} nodes, {GRAPH.number_of_edges()} edges')

with open(DATASET_DIR / 'incidents_history.json', 'r', encoding='utf-8') as f:
    _hist_data = json.load(f)
HISTORY = _hist_data['incidents']
logger.info(f'History loaded: {len(HISTORY)} incidents')

GRAPH_VERSION = f'chaos-{GRAPH.number_of_nodes()}n{GRAPH.number_of_edges()}e'

# ==============================================================================
# Anomaly Detector (background thread)
# ==============================================================================

from detector import AnomalyDetector

detector = AnomalyDetector()
detector.start()

# ==============================================================================
# Correlator + RCA (reuse logic from W2)
# ==============================================================================

from correlate_engine import correlate_alerts, build_clusters
from rca_engine import run_rca

# ==============================================================================
# Prometheus Metrics
# ==============================================================================

try:
    from prometheus_client import Counter, Histogram, make_asgi_app
    REQUEST_COUNT = Counter('aiops_requests_total', 'Total requests', ['endpoint', 'status'])
    REQUEST_LATENCY = Histogram('aiops_request_latency_seconds', 'Request latency', ['endpoint'])
    app.mount('/metrics', make_asgi_app())
    logger.info('Prometheus metrics enabled at /metrics')
except ImportError:
    logger.warning('prometheus-client not installed — /metrics disabled')

# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class CorrelateRequest(BaseModel):
    window: int = Field(default=300, description='Time window in seconds')
    since: Optional[float] = Field(default=None, description='Unix timestamp')

class RCARequest(BaseModel):
    cluster_id: Optional[str] = Field(default=None, description='Cluster ID to analyze')
    alerts: Optional[list[dict]] = Field(default=None, description='Alert list to analyze')

class BaselineUpload(BaseModel):
    baseline: dict

# ==============================================================================
# Middleware
# ==============================================================================

@app.middleware('http')
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers['X-Response-Time-Ms'] = f'{duration_ms:.1f}'
    return response

# ==============================================================================
# Endpoints
# ==============================================================================

@app.get('/healthz')
def healthz():
    return {'status': 'ok', 'version': APP_VERSION}


@app.get('/readyz')
def readyz():
    checks = {
        'graph': GRAPH.number_of_nodes() > 0,
        'history': len(HISTORY) > 0,
        'detector': detector._running,
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)
    return {'status': 'ready', 'checks': checks}


@app.get('/version')
def version():
    return {
        'app': APP_VERSION,
        'graph_version': GRAPH_VERSION,
        'graph_nodes': GRAPH.number_of_nodes(),
        'graph_edges': GRAPH.number_of_edges(),
        'alerts_in_store': len(detector.alerts),
        'detector_running': detector._running,
    }


@app.get('/alerts')
def get_alerts(since: float = Query(default=0, description='Unix timestamp')):
    """List alerts fired since the given Unix timestamp."""
    alerts = detector.get_alerts(since=since)
    return {
        'count': len(alerts),
        'since': since,
        'alerts': alerts,
    }


@app.delete('/alerts')
def clear_alerts():
    """Clear all stored alerts."""
    detector.clear_alerts()
    return {'status': 'cleared'}


@app.post('/correlate')
def correlate_endpoint(req: CorrelateRequest):
    """
    Cluster recent alerts using topology-aware correlation.
    Uses alerts from the last `window` seconds (or since `since` timestamp).
    """
    if req.since is not None:
        alerts = detector.get_alerts(since=req.since)
    else:
        since_ts = time.time() - req.window
        alerts = detector.get_alerts(since=since_ts)

    if not alerts:
        return {'clusters': [], 'alert_count': 0}

    clusters = correlate_alerts(alerts, GRAPH)
    return {
        'alert_count': len(alerts),
        'cluster_count': len(clusters),
        'clusters': clusters,
    }


@app.post('/rca')
def rca_endpoint(req: RCARequest):
    """
    Run Root Cause Analysis on a cluster of alerts.
    Can specify cluster_id (from /correlate output) or provide raw alerts.
    """
    # Get alerts to analyze
    if req.alerts:
        target_alerts = req.alerts
    elif req.cluster_id:
        # Find cluster from recent correlation
        all_alerts = detector.get_alerts(since=time.time() - 600)
        clusters = correlate_alerts(all_alerts, GRAPH)
        target_cluster = None
        for c in clusters:
            if c['cluster_id'] == req.cluster_id:
                target_cluster = c
                break
        if not target_cluster:
            raise HTTPException(status_code=404, detail=f'Cluster {req.cluster_id} not found')
        # Get alerts belonging to this cluster
        cluster_alert_ids = set(target_cluster.get('alert_ids', []))
        target_alerts = [a for a in all_alerts if a['id'] in cluster_alert_ids]
        if not target_alerts:
            target_alerts = all_alerts
    else:
        # Default: use all recent alerts
        target_alerts = detector.get_alerts(since=time.time() - 300)

    if not target_alerts:
        return {
            'root_service': 'unknown',
            'confidence': 0.0,
            'evidence': 'No alerts to analyze',
        }

    result = run_rca(target_alerts, GRAPH, HISTORY)
    return result


@app.post('/baseline')
def upload_baseline(req: BaselineUpload):
    """Upload baseline metrics for improved anomaly detection."""
    detector.set_baseline(req.baseline)
    return {'status': 'baseline_set', 'services': len(req.baseline)}
