"""
RCA Engine — Root Cause Analysis for Chaos Engineering Pipeline
================================================================
Adapted from W2 RCA module. Combines:
  1. Graph + Temporal scoring (sink detection + PageRank + timestamp order)
  2. Similar incident retrieval (keyword similarity)
  3. kNN classification
  4. Hallucination guard (validation)
"""

import logging
from datetime import datetime, timezone
from collections import defaultdict

import networkx as nx

logger = logging.getLogger('aiops.rca')

VALID_CLASSES = {
    'connection_pool_exhaustion', 'slow_query', 'memory_leak',
    'network_partition', 'bad_deploy', 'config_push', 'tls_expiry',
    'thread_starvation', 'cache_stampede', 'downstream_provider',
    'other',
}


def run_rca(alerts: list[dict], graph: nx.DiGraph,
            history: list[dict]) -> dict:
    """
    Full RCA pipeline:
      1. Build cluster from alerts
      2. Graph + Temporal scoring → top candidates
      3. Retrieve similar incidents
      4. Classify from nearest incident
      5. Validate output
      6. Return structured result
    """
    if not alerts:
        return {
            'root_service': 'unknown',
            'confidence': 0.0,
            'evidence': 'No alerts to analyze',
        }

    # Build a pseudo-cluster from the alerts
    services = sorted(set(a['service'] for a in alerts))
    cluster = {
        'cluster_id': 'rca-live',
        'alert_count': len(alerts),
        'services': services,
        'time_range': [
            min(a['ts'] for a in alerts),
            max(a['ts'] for a in alerts),
        ],
        'max_severity': _max_severity(alerts),
        'alert_ids': [a['id'] for a in alerts],
    }

    # ── Step 1: Graph + Temporal scoring ──
    candidates = _graph_temporal_rca(cluster, alerts, graph)
    logger.info(f'RCA candidates: {candidates[:3]}')

    # ── Step 2: Retrieve similar incidents ──
    similar = _retrieve_similar(cluster, history)
    logger.info(f'Similar incidents: {[(s[0]["id"], s[1]) for s in similar[:3]]}')

    # ── Step 3: Classify ──
    classification = _classify(similar)

    # ── Step 4: Build result ──
    root_service = candidates[0][0] if candidates else services[0]
    confidence = candidates[0][1] if candidates else 0.5

    # ── Step 5: Validate ──
    if root_service not in services:
        root_service = services[0]
        confidence = min(confidence, 0.5)

    confidence = max(0.0, min(1.0, confidence))

    # Build evidence
    evidence_parts = [
        f'Top RCA candidate: {root_service} (score={confidence:.2f})',
        f'Services in cluster: {services}',
        f'Alert count: {len(alerts)}',
    ]
    if similar:
        top_inc = similar[0][0]
        evidence_parts.append(
            f'Similar to {top_inc["id"]}: {top_inc["summary"]}'
        )
    evidence_parts.append(f'Classification: {classification["class"]}')
    evidence_parts.append(f'Actions: {classification["actions"]}')

    return {
        'root_service': root_service,
        'confidence': round(confidence, 2),
        'evidence': '; '.join(evidence_parts),
        'classification': classification['class'],
        'actions': classification['actions'],
        'reasoning': classification.get('reasoning', ''),
        'candidates': [
            {'service': svc, 'score': round(score, 3)}
            for svc, score in candidates[:5]
        ],
        'similar_incidents': [
            {'id': inc['id'], 'similarity': round(sim, 2)}
            for inc, sim in similar[:3]
        ],
    }


# ── Graph + Temporal RCA ──

def _graph_temporal_rca(cluster: dict, alerts: list[dict],
                        graph: nx.DiGraph, top_k: int = 5) -> list:
    services = cluster['services']

    if len(services) == 1:
        return [(services[0], 1.0)]

    alert_nodes = [s for s in services if s in graph]
    if not alert_nodes:
        return [(services[0], 0.5)]

    subgraph = graph.subgraph(alert_nodes).copy()

    # Sink detection
    sink_score = {}
    for s in alert_nodes:
        out_deg = subgraph.out_degree(s)
        sink_score[s] = 1.0 if out_deg == 0 else 0.0

    # PageRank on reverse subgraph
    reverse_sub = subgraph.reverse(copy=True)
    try:
        pr = nx.pagerank(reverse_sub, alpha=0.85)
    except Exception:
        pr = {s: 1.0 / len(alert_nodes) for s in alert_nodes}
    max_pr = max(pr.values()) if pr else 1.0
    pr_norm = {s: pr.get(s, 0) / max_pr if max_pr > 0 else 0.5 for s in alert_nodes}

    # Timestamp scoring
    timestamps = {}
    for svc in alert_nodes:
        svc_alerts = [a for a in alerts if a['service'] == svc]
        if svc_alerts:
            earliest = min(_parse_ts(a['ts']) for a in svc_alerts)
            timestamps[svc] = earliest

    if len(timestamps) > 1:
        min_ts = min(timestamps.values())
        max_ts = max(timestamps.values())
        ts_range = (max_ts - min_ts).total_seconds()
        if ts_range > 0:
            ts_score = {
                s: 1.0 - (ts - min_ts).total_seconds() / ts_range
                for s, ts in timestamps.items()
            }
        else:
            ts_score = {s: 1.0 for s in timestamps}
    else:
        ts_score = {s: 1.0 for s in alert_nodes}

    # Severity scoring — higher severity services get bonus
    sev_map = {'info': 0.2, 'warn': 0.4, 'crit': 0.8, 'critical': 1.0}
    sev_score = {}
    for svc in alert_nodes:
        svc_alerts = [a for a in alerts if a['service'] == svc]
        if svc_alerts:
            max_sev = max(sev_map.get(a.get('severity', 'info'), 0.2) for a in svc_alerts)
            sev_score[svc] = max_sev
        else:
            sev_score[svc] = 0.5

    # Combine: sink(0.25) + timestamp(0.30) + pagerank(0.20) + severity(0.25)
    final = {}
    for s in alert_nodes:
        final[s] = (
            0.25 * sink_score.get(s, 0)
            + 0.30 * ts_score.get(s, 0.5)
            + 0.20 * pr_norm.get(s, 0.5)
            + 0.25 * sev_score.get(s, 0.5)
        )

    ranked = sorted(final.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


# ── Similar Incident Retrieval ──

def _retrieve_similar(cluster: dict, incidents: list, top_k: int = 3) -> list:
    cluster_services = set(cluster['services'])
    scored = []
    for inc in incidents:
        score = 0.0
        if inc.get('root_cause_service') in cluster_services:
            score += 0.4
        overlap = set(inc.get('services_involved', [])) & cluster_services
        score += min(len(overlap) * 0.2, 0.4)
        sev_map = {'low': 0, 'medium': 1, 'warn': 2, 'high': 2, 'crit': 3, 'critical': 3}
        c_sev = sev_map.get(cluster.get('max_severity', ''), -1)
        i_sev = sev_map.get(inc.get('severity', ''), -2)
        if c_sev == i_sev and c_sev >= 0:
            score += 0.2
        if score >= 0.2:
            scored.append((inc, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ── Classifier ──

def _classify(similar: list) -> dict:
    if not similar:
        return {
            'class': 'other',
            'actions': ['Investigate manually'],
            'reasoning': 'No similar historical incident found.',
        }
    top1, score = similar[0]
    remediation = top1.get('remediation', 'Investigate manually')
    actions = [a.strip().rstrip('.') for a in remediation.split('. ') if a.strip()]
    if not actions:
        actions = ['Investigate manually']

    return {
        'class': top1.get('root_cause_class', 'other'),
        'actions': actions,
        'reasoning': (
            f'Matched {top1["id"]} (sim={score:.2f}): {top1["summary"]}'
        ),
    }


# ── Helpers ──

def _parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


def _max_severity(alerts: list[dict]) -> str:
    sev_order = {'info': 0, 'warn': 1, 'crit': 2, 'critical': 3}
    if not alerts:
        return 'info'
    return max(alerts, key=lambda a: sev_order.get(a.get('severity', 'info'), 0)).get('severity', 'info')
