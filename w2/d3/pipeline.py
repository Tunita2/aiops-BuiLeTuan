"""
Pipeline Glue Layer — wire Correlate (D1) + RCA (D2) thành 1 unit
=================================================================
- Load service graph + incident history 1 lần lúc import (module-level cache).
- process_batch(alerts) chạy end-to-end: correlate → RCA → structured output.
"""

import json
import logging
import os
from pathlib import Path

from correlate import correlate, build_service_graph
from rca import (
    build_service_graph as rca_build_graph,
    rca_graph_temporal,
    retrieve_similar_incidents,
    classify_from_similar,
    validate_output,
)

logger = logging.getLogger('aiops.pipeline')

# ==============================================================================
# Module-level initialization — chạy 1 lần khi import
# ==============================================================================

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = BASE_DIR / 'dataset'

# Load service graph
_services_path = DATASET_DIR / 'services.json'
with open(_services_path, 'r', encoding='utf-8') as f:
    _services_data = json.load(f)

GRAPH = build_service_graph(_services_data)
logger.info(
    f"Service graph loaded: {GRAPH.number_of_nodes()} nodes, "
    f"{GRAPH.number_of_edges()} edges"
)

# Load incident history
_history_path = DATASET_DIR / 'incidents_history.json'
with open(_history_path, 'r', encoding='utf-8') as f:
    _history_data = json.load(f)

HISTORY = _history_data['incidents']
logger.info(f"Incident history loaded: {len(HISTORY)} incidents")

# Graph metadata for /version endpoint
GRAPH_VERSION = f"g-manual-{GRAPH.number_of_nodes()}n{GRAPH.number_of_edges()}e"
GRAPH_LOADED_AT = None  # Set after import
import datetime as _dt
GRAPH_LOADED_AT = _dt.datetime.now(_dt.timezone.utc).isoformat()


# ==============================================================================
# Pipeline: process_batch
# ==============================================================================

def process_batch(alerts: list[dict]) -> dict:
    """
    Full end-to-end pipeline.
    Input: list of alert dicts (plain dict, không phụ thuộc Pydantic).
    Output: dict matching IncidentResponse schema.

    Flow:
      1. correlate(alerts, GRAPH) → list of clusters
      2. Pick primary cluster (largest by alert_count)
      3. RCA trên primary cluster: graph+temporal scoring → retrieve similar → classify
      4. Pack kết quả thành IncidentResponse-compatible dict
    """
    # --- Layer 1: Correlate ---
    clusters = correlate(alerts, GRAPH, gap_sec=120, max_hop=2)
    logger.info(f"Correlate produced {len(clusters)} clusters from {len(alerts)} alerts")

    if not clusters:
        return {
            'clusters': [],
            'root_cause': {
                'service': 'unknown',
                'confidence': 0.0,
                'reasoning': 'No clusters formed from input alerts',
            },
            'recommended_actions': [],
            'similar_incidents': [],
        }

    # --- Pick primary incident = cluster lớn nhất ---
    primary = max(clusters, key=lambda c: c['alert_count'])
    logger.info(
        f"Primary cluster: {primary['cluster_id']} "
        f"({primary['alert_count']} alerts, services={primary['services']})"
    )

    # --- Layer 2: RCA trên primary cluster ---
    # Step 2a: Graph + Temporal scoring
    candidates = rca_graph_temporal(primary, alerts, GRAPH)

    # Step 2b: Retrieve similar incidents
    similar = retrieve_similar_incidents(primary, HISTORY)

    # Step 2c: Classify from kNN
    classification = classify_from_similar(similar)

    # Step 2d: Build RCA result
    root_cause_service = candidates[0][0] if candidates else primary['services'][0]
    confidence = candidates[0][1] if candidates else 0.5

    rca_result = {
        'cluster_id': primary['cluster_id'],
        'root_cause': root_cause_service,
        'class': classification['class'],
        'confidence': round(confidence, 2),
        'actions': classification['actions'],
        'reasoning': classification['reasoning'],
        'similar_incidents': classification['similar_ids'],
        'method': 'graph+knn',
    }

    # Step 2e: Validate (hallucination guard)
    rca_result = validate_output(rca_result, primary)

    logger.info(
        f"RCA result: root_cause={rca_result['root_cause']}, "
        f"confidence={rca_result['confidence']}, class={rca_result['class']}"
    )

    # --- Pack output matching IncidentResponse schema ---
    return {
        'clusters': [
            {
                'cluster_id': c['cluster_id'],
                'alert_count': c['alert_count'],
                'services': c['services'],
                'time_range': c['time_range'],
            }
            for c in clusters
        ],
        'root_cause': {
            'service': rca_result['root_cause'],
            'confidence': rca_result['confidence'],
            'reasoning': rca_result.get('reasoning', ''),
        },
        'recommended_actions': rca_result.get('actions', []),
        'similar_incidents': [
            {
                'id': inc_id,
                'similarity': round(
                    next(
                        (sim for inc, sim in similar if inc['id'] == inc_id),
                        0.0,
                    ), 2
                ),
                'summary': next(
                    (inc.get('summary', '') for inc, _ in similar if inc['id'] == inc_id),
                    '',
                ),
            }
            for inc_id in rca_result.get('similar_incidents', [])[:3]
        ],
    }
