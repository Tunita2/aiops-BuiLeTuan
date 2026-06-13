"""
AIOps Incident Pipeline — FastAPI Serving Layer
=================================================
Endpoints:
  GET  /healthz   — Liveness check
  GET  /readyz    — Readiness check (graph + history loaded?)
  GET  /version   — App + pipeline config + graph metadata
  POST /incident  — Nhận batch alert → correlate → RCA → JSON response
  GET  /metrics   — Prometheus metrics (auto-mounted)
"""

import json
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

# ==============================================================================
# Structured JSON Logging
# ==============================================================================

class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines — dễ ship vào ELK / Loki."""

    def format(self, record):
        obj = {
            'ts': self.formatTime(record),
            'level': record.levelname,
            'msg': record.getMessage(),
            'logger': record.name,
        }
        if hasattr(record, 'extra'):
            obj.update(record.extra)
        return json.dumps(obj, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())

root_logger = logging.getLogger('aiops')
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

logger = logging.getLogger('aiops.serve')

# ==============================================================================
# Feature Flag — LLM kill switch
# ==============================================================================

USE_LLM = os.environ.get('AIOPS_USE_LLM', 'true').lower() == 'true'

# ==============================================================================
# FastAPI App
# ==============================================================================

APP_VERSION = '1.0.0'

app = FastAPI(
    title='AIOps Incident Pipeline',
    version=APP_VERSION,
    description='Correlate alerts → RCA → suggest action. '
                'Production-ready serving layer for AIOps pipeline.',
)

# ==============================================================================
# Pydantic Schemas — Input
# ==============================================================================

class Alert(BaseModel):
    id: str
    ts: str
    service: str
    metric: str
    severity: str
    value: float
    threshold: float
    labels: Optional[dict] = Field(default_factory=dict)


class IncidentRequest(BaseModel):
    alerts: list[Alert]

# ==============================================================================
# Pydantic Schemas — Output
# ==============================================================================

class Cluster(BaseModel):
    cluster_id: str
    alert_count: int
    services: list[str]
    time_range: list[str]


class RootCause(BaseModel):
    service: str
    confidence: float
    reasoning: str


class SimilarIncident(BaseModel):
    id: str
    similarity: float
    summary: str


class IncidentResponse(BaseModel):
    clusters: list[Cluster]
    root_cause: RootCause
    recommended_actions: list[str]
    similar_incidents: list[SimilarIncident]

# ==============================================================================
# Import Pipeline (lazy — sau khi logging đã setup)
# ==============================================================================

from pipeline import process_batch, GRAPH, HISTORY, GRAPH_VERSION, GRAPH_LOADED_AT

# ==============================================================================
# Prometheus Metrics
# ==============================================================================

try:
    from prometheus_client import Counter, Histogram, make_asgi_app

    REQUEST_COUNT = Counter(
        'aiops_incident_requests_total', 'Total requests', ['status']
    )
    REQUEST_LATENCY = Histogram(
        'aiops_incident_latency_seconds', 'Pipeline latency'
    )
    LLM_FAILURES = Counter(
        'aiops_llm_failures_total', 'LLM failures', ['reason']
    )
    CLUSTER_COUNT = Histogram(
        'aiops_clusters_per_request', 'Clusters per request'
    )

    app.mount('/metrics', make_asgi_app())
    _prometheus_available = True
    logger.info("Prometheus metrics enabled at /metrics")
except ImportError:
    _prometheus_available = False
    logger.warning("prometheus-client not installed — /metrics disabled")

# ==============================================================================
# Middleware — Latency Measurement
# ==============================================================================

@app.middleware('http')
async def add_timing(request: Request, call_next):
    """Đo latency mỗi request, ghi log + gắn header X-Response-Time-Ms."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers['X-Response-Time-Ms'] = f'{duration_ms:.1f}'
    logger.info(
        f"{request.method} {request.url.path} {response.status_code} {duration_ms:.0f}ms",
        extra={'extra': {
            'method': request.method,
            'path': str(request.url.path),
            'status': response.status_code,
            'duration_ms': round(duration_ms, 1),
        }}
    )
    return response

# ==============================================================================
# Endpoints
# ==============================================================================

@app.get('/healthz')
def healthz() -> dict:
    """Liveness check — process còn sống không."""
    return {'status': 'ok'}


@app.get('/readyz')
def readyz() -> dict:
    """
    Readiness check — sẵn sàng nhận traffic chưa?
    Check: graph loaded + history loaded.
    LLM check bỏ qua — readiness không nên depend external service quá chặt.
    """
    checks = {
        'graph': GRAPH.number_of_nodes() > 0,
        'history': len(HISTORY) > 0,
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)
    return {'status': 'ready', 'checks': checks}


@app.get('/version')
def version() -> dict:
    """App version + pipeline config + graph metadata."""
    return {
        'app': APP_VERSION,
        'pipeline_config': {
            'correlate_gap_sec': 120,
            'correlate_max_hop': 2,
            'rca_method': 'graph+knn',
            'use_llm': USE_LLM,
        },
        'graph_version': GRAPH_VERSION,
        'graph_loaded_at': GRAPH_LOADED_AT,
        'graph_node_count': GRAPH.number_of_nodes(),
        'graph_edge_count': GRAPH.number_of_edges(),
    }


@app.post('/incident', response_model=IncidentResponse)
def post_incident(req: IncidentRequest) -> IncidentResponse:
    """
    Nhận batch alert → chạy pipeline end-to-end → trả incident report.
    - Empty alerts → 400
    - Pipeline exception → 500 (log full traceback, không leak ra client)
    """
    if not req.alerts:
        raise HTTPException(status_code=400, detail='Empty alert list')

    alerts_dict = [a.model_dump() for a in req.alerts]
    logger.info(
        f"Received {len(alerts_dict)} alerts",
        extra={'extra': {'alert_count': len(alerts_dict)}}
    )

    try:
        if _prometheus_available:
            with REQUEST_LATENCY.time():
                result = process_batch(alerts_dict)
                REQUEST_COUNT.labels(status='success').inc()
                CLUSTER_COUNT.observe(len(result['clusters']))
        else:
            result = process_batch(alerts_dict)

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        if _prometheus_available:
            REQUEST_COUNT.labels(status='error').inc()
        raise HTTPException(status_code=500, detail=f'Pipeline error: {type(e).__name__}')

    logger.info(
        'Processed incident',
        extra={'extra': {
            'cluster_count': len(result['clusters']),
            'root_cause': result['root_cause']['service'],
            'confidence': result['root_cause']['confidence'],
        }}
    )

    return IncidentResponse(**result)
