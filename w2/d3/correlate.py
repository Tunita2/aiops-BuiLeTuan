"""
Alert Correlator — 4-Layer Pipeline
====================================
Layer 1: Dedup        — Gộp alert trùng lặp bằng fingerprint
Layer 2: Time-Window  — Chia alert thành các session (gap_sec)
Layer 3: Topology     — Gom alert theo khoảng cách service trên graph (max_hop)
Layer 4: Semantic      — (Bonus) Gom alert có metric name tương đồng về ngữ nghĩa
"""

import json
import networkx as nx
from datetime import datetime, timezone
from collections import defaultdict


# ==============================================================================
# Layer 1: Deduplication
# ==============================================================================

def fingerprint(alert: dict) -> str:
    """
    Tạo 'vân tay' cho alert dựa trên 3 trường cố định: service, metric, severity.
    Không include timestamp hay value vì chúng thay đổi mỗi lần fire.
    """
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


class Deduper:
    """
    Bộ khử trùng lặp — lưu trạng thái (state) để nhận diện alert giống nhau.
    - store: dictionary { fingerprint -> thông tin tóm tắt }
    """

    def __init__(self):
        self.store: dict[str, dict] = {}

    def push(self, alert: dict) -> str:
        """Nhận 1 alert, trả về fingerprint. Nếu đã có thì tăng count."""
        fp = fingerprint(alert)
        if fp not in self.store:
            self.store[fp] = {
                'cluster_id': fp,
                'count': 1,
                'first_seen': alert['ts'],
                'last_seen': alert['ts'],
                'alerts': [alert],
            }
        else:
            c = self.store[fp]
            c['count'] += 1
            c['last_seen'] = alert['ts']
            c['alerts'].append(alert)
        return fp

    def get_deduped_alerts(self) -> list[dict]:
        """
        Trả về danh sách alert ĐẠI DIỆN (mỗi fingerprint giữ lại tất cả,
        nhưng đánh dấu count để biết bao nhiêu lần lặp).
        """
        results = []
        for fp, info in self.store.items():
            for alert in info['alerts']:
                alert['_fingerprint'] = fp
                alert['_dedup_count'] = info['count']
            results.extend(info['alerts'])
        return results


# ==============================================================================
# Layer 2: Time-Window (Session Window)
# ==============================================================================

def parse_ts(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp string thành datetime object."""
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


def session_groups(alerts: list[dict], gap_sec: int = 120) -> list[list[dict]]:
    """
    Chia alert thành các 'session'. 
    Session tự động ngắt khi khoảng cách giữa 2 alert liên tiếp > gap_sec giây.
    
    - gap_sec = 120 (2 phút): sweet spot cho hầu hết production system.
    """
    if not alerts:
        return []

    sorted_alerts = sorted(alerts, key=lambda a: a['ts'])
    groups = [[sorted_alerts[0]]]

    for alert in sorted_alerts[1:]:
        last_ts = parse_ts(groups[-1][-1]['ts'])
        curr_ts = parse_ts(alert['ts'])
        if (curr_ts - last_ts).total_seconds() <= gap_sec:
            groups[-1].append(alert)
        else:
            groups.append([alert])

    return groups


# ==============================================================================
# Layer 4 (Bonus): Semantic Similarity
# ==============================================================================

def text_similarity(a: dict, b: dict) -> float:
    """
    Tính Jaccard similarity giữa 2 alert dựa trên tokenized metric name + labels note.
    Giúp phát hiện alert khác tên nhưng cùng chủ đề (vd: db_pool_used_ratio vs db_connection_count).
    """
    def tokens(x):
        text = f"{x['metric']} {x.get('labels', {}).get('note', '')}"
        return set(text.lower().replace('_', ' ').split())

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def semantic_merge(alerts: list[dict], threshold: float = 0.4) -> list[dict]:
    """
    Gộp alert có semantic similarity cao (>= threshold) — đánh dấu cùng _semantic_group.
    Sử dụng Union-Find để gom nhóm.
    """
    n = len(alerts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            # Chỉ merge semantic nếu CÙNG service (tránh gom nhầm cross-service)
            if alerts[i]['service'] == alerts[j]['service']:
                if text_similarity(alerts[i], alerts[j]) >= threshold:
                    union(i, j)

    # Gán _semantic_group cho mỗi alert
    for i in range(n):
        alerts[i]['_semantic_group'] = find(i)

    return alerts


# ==============================================================================
# Layer 3: Topology Grouping
# ==============================================================================

def build_service_graph(services_data: dict) -> nx.DiGraph:
    """
    Dựng đồ thị dịch vụ (directed graph) từ file services.json.
    Node = service/store, Edge = mối quan hệ gọi nhau.
    """
    G = nx.DiGraph()

    # Thêm tất cả service nodes
    for svc in services_data.get('services', []):
        G.add_node(svc['name'], **svc)

    # Thêm tất cả store nodes
    for store in services_data.get('stores', []):
        G.add_node(store['name'], **store)

    # Thêm các cạnh (edges)
    for edge in services_data.get('edges', []):
        G.add_edge(edge['from'], edge['to'], type=edge.get('type', 'http'))

    return G


def topology_group(alerts: list[dict], graph: nx.DiGraph, max_hop: int = 2) -> list[list[dict]]:
    """
    Directed-Propagation Topology Grouping.

    Thay vì dùng undirected graph + Union-Find (dễ gom nhầm do transitive),
    thuật toán này tận dụng CHIỀU GỌI của service graph:

    1. Dựng alert subgraph — chỉ giữ node có alert.
    2. Tìm Sinks (out-degree = 0 trong alert subgraph) — đây là Root Cause candidates.
       Ví dụ: payment-svc không gọi service nào khác có alert → sink.
    3. Gộp các sink nếu chúng chia sẻ caller chung (ancestor) thuộc tầng nghiệp vụ
       (không phải edge tier), hoặc có đường gọi trực tiếp ≤ max_hop.
    4. Route các non-sink (caller/symptom) vào sink group mà chúng có thể reach.
    """
    by_service = defaultdict(list)
    for a in alerts:
        by_service[a['service']].append(a)

    alert_services = list(by_service.keys())

    # Nếu chỉ có 1 service → 1 group duy nhất
    if len(alert_services) <= 1:
        return [alerts]

    # --- Bước 1: Dựng alert subgraph (chỉ giữ node có alert) ---
    alert_subgraph = graph.subgraph(alert_services)

    # --- Bước 2: Tìm Sinks (Root Cause candidates) ---
    # Sink = service có out-degree = 0 trong alert subgraph
    # (không gọi service nào khác mà cũng đang alert)
    sinks = [n for n in alert_services if alert_subgraph.out_degree(n) == 0]

    # Nếu không có sink nào (cycle?) → fallback tất cả là sink
    if not sinks:
        sinks = list(alert_services)

    # --- Bước 3: Gộp các sink bằng Union-Find ---
    parent = {s: s for s in sinks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Tìm ancestor (caller) cho mỗi sink trong alert subgraph
    ancestors = {}
    for sink in sinks:
        anc = set()
        for node in alert_services:
            if node != sink and nx.has_path(alert_subgraph, node, sink):
                anc.add(node)
        ancestors[sink] = anc

    # Gộp 2 sink nếu:
    #   (a) Chúng chia sẻ caller chung KHÔNG thuộc tier 'edge'
    #       (tránh gộp nhầm qua edge-lb — edge-lb route mọi thứ)
    #   (b) Có đường gọi trực tiếp giữa chúng ≤ max_hop
    for i, s1 in enumerate(sinks):
        for s2 in sinks[i + 1:]:
            should_merge = False

            # Điều kiện (a): shared non-edge ancestor
            shared = ancestors[s1] & ancestors[s2]
            non_edge_shared = [
                a for a in shared
                if graph.nodes[a].get('tier', '') != 'edge'
            ]
            if non_edge_shared:
                should_merge = True

            # Điều kiện (b): direct call path ≤ max_hop trong alert subgraph
            if not should_merge:
                for src, dst in [(s1, s2), (s2, s1)]:
                    try:
                        if nx.shortest_path_length(alert_subgraph, src, dst) <= max_hop:
                            should_merge = True
                            break
                    except nx.NetworkXNoPath:
                        pass

            if should_merge:
                parent[find(s1)] = find(s2)

    # Tạo sink groups
    sink_groups = defaultdict(list)
    for s in sinks:
        sink_groups[find(s)].append(s)

    # --- Bước 4: Route non-sink nodes vào sink group ---
    severity_order = {'info': 0, 'warn': 1, 'crit': 2, 'critical': 3}

    def max_severity_of(svc):
        """Tính max severity của tất cả alert thuộc service này."""
        svc_alerts = by_service.get(svc, [])
        if not svc_alerts:
            return 0
        return max(severity_order.get(a['severity'], 0) for a in svc_alerts)

    # Khởi tạo cluster: mỗi sink group → 1 cluster chứa các service
    cluster_services = {}  # root_key -> set of services
    for root, group_sinks in sink_groups.items():
        cluster_services[root] = set(group_sinks)

    non_sinks = [n for n in alert_services if n not in sinks]

    for ns in non_sinks:
        # Tìm tất cả sink group mà non-sink này có thể reach
        reachable = []
        for root, group_sinks in sink_groups.items():
            for sink in group_sinks:
                if nx.has_path(alert_subgraph, ns, sink):
                    reachable.append(root)
                    break

        if not reachable:
            # Không reach được sink nào → tạo cluster riêng
            new_key = ns
            cluster_services[new_key] = {ns}
        elif len(reachable) == 1:
            cluster_services[reachable[0]].add(ns)
        else:
            # Reach nhiều group → chọn group có sink severity cao nhất
            best = max(
                reachable,
                key=lambda r: max(max_severity_of(s) for s in sink_groups[r])
            )
            cluster_services[best].add(ns)

    # --- Build output: list[list[dict]] ---
    result = []
    for root, svcs in cluster_services.items():
        group_alerts = []
        for svc in svcs:
            group_alerts.extend(by_service[svc])
        result.append(group_alerts)

    return result


# ==============================================================================
# Main Pipeline: Correlate
# ==============================================================================

def correlate(alerts: list[dict], graph: nx.DiGraph,
              gap_sec: int = 120, max_hop: int = 2) -> list[dict]:
    """
    Pipeline chính — kết hợp 4 layer:
    1. Dedup: Gán fingerprint cho từng alert
    2. Time-Window (Session): Chia alert thành các đợt sự cố
    3. Semantic: Gom alert khác tên nhưng cùng chủ đề (trong mỗi session)
    4. Topology: Gom alert theo khoảng cách service trên graph
    
    Output: danh sách clusters, mỗi cluster có metadata đầy đủ.
    """
    # --- Layer 1: Dedup — gán fingerprint ---
    deduper = Deduper()
    for alert in alerts:
        deduper.push(alert)
    deduped_alerts = deduper.get_deduped_alerts()

    # --- Layer 2: Session Window ---
    sessions = session_groups(deduped_alerts, gap_sec=gap_sec)

    # --- Layer 3 + 4: Với mỗi session, chạy Semantic rồi Topology ---
    clusters = []
    for s_idx, session_alerts in enumerate(sessions):
        # Layer Bonus: Semantic merge trong session
        session_alerts = semantic_merge(session_alerts, threshold=0.4)

        # Layer 3: Topology grouping
        topo_groups = topology_group(session_alerts, graph, max_hop)

        for g_idx, group in enumerate(topo_groups):
            severity_order = {'info': 0, 'warn': 1, 'crit': 2, 'critical': 3}
            max_sev = max(group, key=lambda a: severity_order.get(a['severity'], 0))

            clusters.append({
                'cluster_id': f'c-{s_idx:03d}-{g_idx:03d}',
                'alert_count': len(group),
                'services': sorted(set(a['service'] for a in group)),
                'time_range': [
                    min(a['ts'] for a in group),
                    max(a['ts'] for a in group),
                ],
                'max_severity': max_sev['severity'],
                'alert_ids': [a['id'] for a in group],
                'fingerprints': sorted(set(
                    a.get('_fingerprint', fingerprint(a)) for a in group
                )),
            })

    return clusters


# ==============================================================================
# Output Builder
# ==============================================================================

def build_summary(alerts: list[dict], clusters: list[dict]) -> dict:
    """Tạo object cluster_summary.json theo đúng format yêu cầu."""
    input_count = len(alerts)
    output_count = len(clusters)
    reduction = round(1 - output_count / input_count, 2) if input_count > 0 else 0

    return {
        'input_alerts': input_count,
        'output_clusters': output_count,
        'reduction_ratio': reduction,
        'clusters': clusters,
    }


# ==============================================================================
# Entrypoint — chạy trực tiếp từ terminal
# ==============================================================================

if __name__ == '__main__':
    import os

    # Đường dẫn dataset
    base_dir = os.path.dirname(os.path.abspath(__file__))
    alerts_path = os.path.join(base_dir, 'dataset', 'alerts_sample.jsonl')
    services_path = os.path.join(base_dir, 'dataset', 'services.json')
    output_path = os.path.join(base_dir, 'results', 'cluster_summary.json')

    # Load alerts
    with open(alerts_path, 'r', encoding='utf-8') as f:
        raw_alerts = [json.loads(line) for line in f if line.strip()]

    # Load service graph
    with open(services_path, 'r', encoding='utf-8') as f:
        services_data = json.load(f)
    graph = build_service_graph(services_data)

    # Chạy pipeline
    clusters = correlate(raw_alerts, graph, gap_sec=120, max_hop=2)
    summary = build_summary(raw_alerts, clusters)

    # Ghi output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # In kết quả
    print(f"Input alerts:   {summary['input_alerts']}")
    print(f"Output clusters: {summary['output_clusters']}")
    print(f"Reduction ratio: {summary['reduction_ratio']}")
    print()
    for c in summary['clusters']:
        print(f"  [{c['cluster_id']}] {c['alert_count']} alerts | "
              f"services={c['services']} | severity={c['max_severity']} | "
              f"time={c['time_range']}")
