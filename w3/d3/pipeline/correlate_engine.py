"""
Correlate Engine — Alert Correlation for Chaos Engineering Pipeline
====================================================================
Simplified from W2 correlator — focused on the 3 layers needed for chaos:
  Layer 1: Dedup (fingerprint)
  Layer 2: Time-Window (session)
  Layer 3: Topology (graph-aware grouping)
"""

import networkx as nx
from datetime import datetime, timezone
from collections import defaultdict


def fingerprint(alert: dict) -> str:
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


def parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))


def correlate_alerts(alerts: list[dict], graph: nx.DiGraph,
                     gap_sec: int = 120, max_hop: int = 2) -> list[dict]:
    """
    Full correlation pipeline: dedup → time-window → topology grouping.
    Returns list of cluster dicts.
    """
    if not alerts:
        return []

    # ── Layer 1: Dedup ──
    seen_fps = {}
    deduped = []
    for a in alerts:
        fp = fingerprint(a)
        a['_fingerprint'] = fp
        if fp not in seen_fps:
            seen_fps[fp] = {'count': 0, 'first': a['ts']}
        seen_fps[fp]['count'] += 1
        a['_dedup_count'] = seen_fps[fp]['count']
        deduped.append(a)

    # ── Layer 2: Time-Window ──
    sessions = _session_groups(deduped, gap_sec)

    # ── Layer 3: Topology grouping ──
    clusters = []
    for s_idx, session_alerts in enumerate(sessions):
        topo_groups = _topology_group(session_alerts, graph, max_hop)
        for g_idx, group in enumerate(topo_groups):
            sev_order = {'info': 0, 'warn': 1, 'crit': 2, 'critical': 3}
            max_sev_alert = max(group, key=lambda a: sev_order.get(a.get('severity', ''), 0))
            clusters.append({
                'cluster_id': f'c-{s_idx:03d}-{g_idx:03d}',
                'alert_count': len(group),
                'services': sorted(set(a['service'] for a in group)),
                'time_range': [
                    min(a['ts'] for a in group),
                    max(a['ts'] for a in group),
                ],
                'max_severity': max_sev_alert.get('severity', 'warn'),
                'alert_ids': [a['id'] for a in group],
                'fingerprints': sorted(set(a.get('_fingerprint', '') for a in group)),
            })

    return clusters


def build_clusters(alerts: list[dict], graph: nx.DiGraph) -> list[dict]:
    """Alias for correlate_alerts."""
    return correlate_alerts(alerts, graph)


# ── Internal helpers ──

def _session_groups(alerts: list[dict], gap_sec: int) -> list[list[dict]]:
    if not alerts:
        return []
    sorted_alerts = sorted(alerts, key=lambda a: a['ts'])
    groups = [[sorted_alerts[0]]]
    for alert in sorted_alerts[1:]:
        try:
            last_ts = parse_ts(groups[-1][-1]['ts'])
            curr_ts = parse_ts(alert['ts'])
            if (curr_ts - last_ts).total_seconds() <= gap_sec:
                groups[-1].append(alert)
            else:
                groups.append([alert])
        except Exception:
            groups[-1].append(alert)
    return groups


def _topology_group(alerts: list[dict], graph: nx.DiGraph,
                    max_hop: int) -> list[list[dict]]:
    by_service = defaultdict(list)
    for a in alerts:
        by_service[a['service']].append(a)

    alert_services = list(by_service.keys())
    if len(alert_services) <= 1:
        return [alerts]

    # Build alert subgraph
    valid_nodes = [s for s in alert_services if s in graph]
    if len(valid_nodes) <= 1:
        return [alerts]

    subgraph = graph.subgraph(valid_nodes)

    # Find sinks (out-degree=0 in alert subgraph)
    sinks = [n for n in valid_nodes if subgraph.out_degree(n) == 0]
    if not sinks:
        sinks = list(valid_nodes)

    # Union-Find for merging sink groups
    parent = {s: s for s in sinks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Merge sinks with shared ancestors or direct paths
    for i, s1 in enumerate(sinks):
        for s2 in sinks[i + 1:]:
            should_merge = False
            # Check direct path
            for src, dst in [(s1, s2), (s2, s1)]:
                try:
                    if nx.shortest_path_length(subgraph, src, dst) <= max_hop:
                        should_merge = True
                        break
                except nx.NetworkXNoPath:
                    pass
            # Check shared ancestor
            if not should_merge:
                anc1 = {n for n in valid_nodes if n != s1 and nx.has_path(subgraph, n, s1)}
                anc2 = {n for n in valid_nodes if n != s2 and nx.has_path(subgraph, n, s2)}
                if anc1 & anc2:
                    should_merge = True
            if should_merge:
                parent[find(s1)] = find(s2)

    # Build sink groups
    sink_groups = defaultdict(list)
    for s in sinks:
        sink_groups[find(s)].append(s)

    # Route non-sink nodes
    cluster_services = {root: set(group) for root, group in sink_groups.items()}
    non_sinks = [n for n in valid_nodes if n not in sinks]

    for ns in non_sinks:
        reachable = []
        for root, group_sinks in sink_groups.items():
            for sink in group_sinks:
                if nx.has_path(subgraph, ns, sink):
                    reachable.append(root)
                    break
        if not reachable:
            cluster_services[ns] = {ns}
        else:
            cluster_services[reachable[0]].add(ns)

    # Also add non-graph services
    for svc in alert_services:
        if svc not in valid_nodes:
            cluster_services.setdefault(svc, set()).add(svc)

    # Build output
    result = []
    for root, svcs in cluster_services.items():
        group_alerts = []
        for svc in svcs:
            group_alerts.extend(by_service.get(svc, []))
        if group_alerts:
            result.append(group_alerts)

    return result if result else [alerts]
