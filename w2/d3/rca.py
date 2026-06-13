"""
RCA Module — Graph Traversal + Temporal Scoring + Keyword Retrieval
===================================================================
Pipeline: cluster → graph+temporal scoring → retrieve similar incidents
         → kNN classify → validate → output rca_output.json

Acceptance criteria:
  - Code dùng networkx (nx) cho graph analysis
  - Code có keyword retrieval (similar, top_k, _similarity)
  - Output JSON hợp lệ với graph_top3, root_cause, class
"""

import json
import os
import sys
import networkx as nx
from datetime import datetime, timezone
from collections import defaultdict

# Fix encoding cho Windows console (skip trong Jupyter)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass


# ==============================================================================
# 1. Build Service Graph
# ==============================================================================

def build_service_graph(services_data: dict) -> nx.DiGraph:
    """
    Dựng đồ thị dịch vụ (directed graph) từ services.json.
    Node = service/store, Edge = mối quan hệ gọi nhau (from → to).
    """
    G = nx.DiGraph()

    for svc in services_data.get('services', []):
        G.add_node(svc['name'], **svc)

    for store in services_data.get('stores', []):
        G.add_node(store['name'], **store)

    for edge in services_data.get('edges', []):
        G.add_edge(edge['from'], edge['to'], type=edge.get('type', 'http'))

    return G


# ==============================================================================
# 2. Graph Traversal + Temporal Scoring RCA
# ==============================================================================

def parse_ts(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp → datetime UTC."""
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


def rca_graph_temporal(cluster: dict, alerts: list, graph: nx.DiGraph,
                       top_k: int = 3) -> list:
    """
    Xếp hạng root cause candidate bằng kết hợp 3 tín hiệu:
      1. Sink detection  — service không gọi ai trong alert subgraph (out_degree=0)
      2. PageRank        — đánh giá tầm quan trọng trên reverse subgraph
      3. Timestamp       — service alert sớm nhất được ưu tiên

    Returns: list of (service_name, score) sorted desc, top_k items.
    """
    services = cluster['services']
    cluster_alert_ids = set(cluster['alert_ids'])
    cluster_alerts = [a for a in alerts if a['id'] in cluster_alert_ids]

    # --- Trường hợp cluster chỉ có 1 service ---
    if len(services) == 1:
        return [(services[0], 1.0)]

    # --- Dựng alert subgraph (chỉ giữ node đang alert) ---
    alert_nodes = [s for s in services if s in graph]
    if not alert_nodes:
        return [(services[0], 0.5)]

    subgraph = graph.subgraph(alert_nodes).copy()

    # --- Tín hiệu 1: Sink detection ---
    # Sink = out_degree = 0 trong alert subgraph → không gọi ai đang alert
    # → cao khả năng là root cause (callee cuối chuỗi)
    sink_score = {}
    for s in alert_nodes:
        out_deg = subgraph.out_degree(s)
        if out_deg == 0:
            sink_score[s] = 1.0  # Là sink → bonus tối đa
        else:
            sink_score[s] = 0.0  # Không phải sink

    # --- Tín hiệu 2: PageRank trên reverse subgraph ---
    # Reverse graph → service được nhiều caller phụ thuộc sẽ có score cao
    reverse_subgraph = subgraph.reverse(copy=True)
    try:
        pr_scores = nx.pagerank(reverse_subgraph, alpha=0.85)
    except Exception:
        pr_scores = {s: 1.0 / len(alert_nodes) for s in alert_nodes}

    # Normalize PageRank về [0, 1]
    max_pr = max(pr_scores.values()) if pr_scores else 1.0
    if max_pr > 0:
        pagerank_norm = {s: pr_scores.get(s, 0) / max_pr for s in alert_nodes}
    else:
        pagerank_norm = {s: 0.5 for s in alert_nodes}

    # --- Tín hiệu 3: Timestamp scoring ---
    # Service alert sớm nhất → score = 1.0, muộn nhất → score = 0.0
    timestamps = {}
    for svc in alert_nodes:
        svc_alerts = [a for a in cluster_alerts if a['service'] == svc]
        if svc_alerts:
            earliest = min(parse_ts(a['ts']) for a in svc_alerts)
            timestamps[svc] = earliest

    if len(timestamps) > 1:
        min_ts = min(timestamps.values())
        max_ts = max(timestamps.values())
        ts_range = (max_ts - min_ts).total_seconds()
        if ts_range > 0:
            timestamp_score = {
                s: 1.0 - (ts - min_ts).total_seconds() / ts_range
                for s, ts in timestamps.items()
            }
        else:
            timestamp_score = {s: 1.0 for s in timestamps}
    elif len(timestamps) == 1:
        timestamp_score = {s: 1.0 for s in timestamps}
    else:
        timestamp_score = {s: 0.5 for s in alert_nodes}

    # --- Kết hợp 3 tín hiệu → final score ---
    # Trọng số: sink (0.3) + timestamp (0.4) + pagerank (0.3)
    # Sink + Timestamp nặng hơn PageRank vì trong thực tế:
    #   - Thủ phạm (culprit) nằm ở cuối chuỗi gọi (sink) VÀ alert sớm nhất
    #   - PageRank reverse ưu tiên hub (checkout-svc) chứ không ưu tiên sink
    final_scores = {}
    for s in alert_nodes:
        sk = sink_score.get(s, 0)
        ts = timestamp_score.get(s, 0)
        pr = pagerank_norm.get(s, 0)
        final_scores[s] = 0.3 * sk + 0.4 * ts + 0.3 * pr

    # Sắp xếp giảm dần theo score
    ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


# ==============================================================================
# 3. Keyword-based Retrieval — tìm incident lịch sử tương tự
# ==============================================================================

def compute_similarity(cluster: dict, incident: dict) -> float:
    """
    Tính heuristic similarity score (0–1) giữa cluster hiện tại và 1 incident lịch sử.

    Scoring:
      +0.4  nếu root_cause_service của incident nằm trong cluster.services
      +0.2  mỗi service overlap (max +0.4)
      +0.2  nếu cùng mức severity
    """
    cluster_services = set(cluster['services'])
    score = 0.0

    # Root cause service match
    if incident['root_cause_service'] in cluster_services:
        score += 0.4

    # Service overlap
    overlap = set(incident['services_involved']) & cluster_services
    score += min(len(overlap) * 0.2, 0.4)

    # Severity match
    sev_map = {
        'low': 0, 'medium': 1, 'warn': 2, 'high': 2, 'crit': 3, 'critical': 3,
    }
    cluster_sev = sev_map.get(cluster.get('max_severity', ''), -1)
    incident_sev = sev_map.get(incident.get('severity', ''), -2)
    if cluster_sev == incident_sev and cluster_sev >= 0:
        score += 0.2

    return score


def retrieve_similar_incidents(cluster: dict, incidents: list,
                               top_k: int = 3) -> list:
    """
    Keyword-based retrieval: tìm top-K incident lịch sử tương tự nhất.
    Chỉ giữ incident có score >= 0.2.

    Returns: list of (incident_dict, similarity_score) sorted desc.
    """
    scored = []
    for inc in incidents:
        sim = compute_similarity(cluster, inc)
        if sim >= 0.2:
            scored.append((inc, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ==============================================================================
# 4. kNN-style Classifier — lấy class + actions từ top-1 similar incident
# ==============================================================================

def classify_from_similar(similar_incidents: list) -> dict:
    """
    Lấy root_cause_class + remediation actions từ incident tương tự nhất.
    Nếu không tìm thấy → fallback class='other', actions=['Investigate manually'].
    """
    if not similar_incidents:
        return {
            'class': 'other',
            'actions': ['Investigate manually'],
            'reasoning': 'No similar historical incident found. Manual investigation required.',
            'similar_ids': [],
        }

    top1_inc, top1_score = similar_incidents[0]

    # Tách remediation thành list actions (split bằng ". ")
    remediation = top1_inc.get('remediation', 'Investigate manually')
    actions = [a.strip().rstrip('.') for a in remediation.split('. ') if a.strip()]
    if not actions:
        actions = ['Investigate manually']

    # Reasoning dựa trên incident tương tự
    reasoning = (
        f"Matched historical incident {top1_inc['id']} (similarity={top1_score:.2f}): "
        f"{top1_inc['summary']}"
    )

    return {
        'class': top1_inc['root_cause_class'],
        'actions': actions,
        'reasoning': reasoning,
        'similar_ids': [inc['id'] for inc, _ in similar_incidents],
    }


# ==============================================================================
# 5. Validation — kiểm tra output hợp lệ trước khi ghi file
# ==============================================================================

VALID_CLASSES = {
    'connection_pool_exhaustion', 'slow_query', 'memory_leak',
    'rebalance_storm', 'deadlock', 'network_partition', 'bad_deploy',
    'config_push', 'tls_expiry', 'ddos', 'other',
    # Mở rộng thêm các class từ incidents_history.json
    'lock_contention', 'eviction', 'infinite_retry', 'model_drift',
    'rate_limit_misconfig', 'vacuum_storm', 'thread_starvation',
    'cache_stampede', 'n_plus_1', 'downstream_provider',
    'batch_overlap', 'feature_flag', 'cache_cold_start',
    'replication_lag', 'data_pipeline_lag',
}


def validate_output(result: dict, cluster: dict) -> dict:
    """
    Validate 4 tiêu chí bắt buộc (Hallucination Guard):
      1. root_cause ∈ cluster.services
      2. class ∈ enum đã định nghĩa
      3. confidence ∈ [0, 1]
      4. actions là list non-empty
    Nếu invalid → fallback an toàn.
    """
    # Check 1: root_cause phải nằm trong cluster services
    if result.get('root_cause') not in cluster['services']:
        result['root_cause'] = cluster['services'][0]
        result['method'] = 'graph-only-fallback'

    # Check 2: class phải thuộc enum
    if result.get('class') not in VALID_CLASSES:
        result['class'] = 'other'

    # Check 3: confidence phải là float trong [0, 1]
    conf = result.get('confidence', 0.5)
    result['confidence'] = round(max(0.0, min(1.0, float(conf))), 2)

    # Check 4: actions phải là list non-empty
    if not result.get('actions') or not isinstance(result['actions'], list):
        result['actions'] = ['Investigate manually']

    return result


# ==============================================================================
# 6. RCA Pipeline — kết nối tất cả các bước
# ==============================================================================

def run_rca_pipeline(cluster_summary: dict, alerts: list,
                     graph: nx.DiGraph, incidents_data: dict) -> dict:
    """
    Pipeline RCA hoàn chỉnh:
      1. Graph + Temporal scoring → top-K candidates
      2. Retrieve similar incidents (keyword similarity)
      3. Classify từ top-1 similar (kNN-style)
      4. Validate output + fallback
      5. Return structured JSON
    """
    clusters = cluster_summary['clusters']
    incidents = incidents_data['incidents']

    results = []
    for cluster in clusters:
        print(f"\n{'='*60}")
        print(f"Analyzing cluster: {cluster['cluster_id']}")
        print(f"  Services: {cluster['services']}")
        print(f"  Alert count: {cluster['alert_count']}")
        print(f"  Max severity: {cluster['max_severity']}")

        # --- Step 1: Graph + Temporal RCA ---
        candidates = rca_graph_temporal(cluster, alerts, graph)
        print(f"\n  [Graph+Temporal] Top candidates:")
        for svc, score in candidates:
            print(f"    {svc}: {score:.3f}")

        # --- Step 2: Retrieve similar incidents ---
        similar = retrieve_similar_incidents(cluster, incidents)
        print(f"\n  [Retrieval] Top similar incidents:")
        for inc, sim in similar:
            print(f"    {inc['id']} (sim={sim:.2f}): {inc['root_cause_class']}")

        # --- Step 3: Classify from kNN ---
        classification = classify_from_similar(similar)
        print(f"\n  [Classifier] class={classification['class']}")
        print(f"  [Classifier] actions={classification['actions']}")

        # --- Step 4: Build result ---
        root_cause = candidates[0][0] if candidates else cluster['services'][0]
        confidence = candidates[0][1] if candidates else 0.5

        result = {
            'cluster_id': cluster['cluster_id'],
            'graph_top3': [[svc, round(score, 2)] for svc, score in candidates[:3]],
            'root_cause': root_cause,
            'class': classification['class'],
            'confidence': round(confidence, 2),
            'actions': classification['actions'],
            'reasoning': classification['reasoning'],
            'similar_incidents': classification['similar_ids'],
            'method': 'graph+knn',
        }

        # --- Step 5: Validate ---
        result = validate_output(result, cluster)
        results.append(result)

        print(f"\n  ✓ Root cause: {result['root_cause']}")
        print(f"  ✓ Class: {result['class']}")
        print(f"  ✓ Confidence: {result['confidence']}")

    output = {
        'clusters_analyzed': len(clusters),
        'results': results,
    }

    return output


# ==============================================================================
# Entrypoint — chạy trực tiếp từ terminal
# ==============================================================================

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_dir = os.path.join(base_dir, 'dataset')
    results_dir = os.path.join(base_dir, 'results')

    # Load data
    with open(os.path.join(dataset_dir, 'cluster_summary.json'), 'r', encoding='utf-8') as f:
        cluster_summary = json.load(f)

    with open(os.path.join(dataset_dir, 'alerts_sample.jsonl'), 'r', encoding='utf-8') as f:
        alerts = [json.loads(line) for line in f if line.strip()]

    with open(os.path.join(dataset_dir, 'services.json'), 'r', encoding='utf-8') as f:
        services_data = json.load(f)

    with open(os.path.join(dataset_dir, 'incidents_history.json'), 'r', encoding='utf-8') as f:
        incidents_data = json.load(f)

    # Build graph
    graph = build_service_graph(services_data)
    print(f"Service graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

    # Run pipeline
    output = run_rca_pipeline(cluster_summary, alerts, graph, incidents_data)

    # Write output
    os.makedirs(results_dir, exist_ok=True)
    output_path = os.path.join(results_dir, 'rca_output.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Output written to: {output_path}")
    print(f"Clusters analyzed: {output['clusters_analyzed']}")
    for r in output['results']:
        print(f"  [{r['cluster_id']}] root_cause={r['root_cause']} "
              f"class={r['class']} confidence={r['confidence']}")
