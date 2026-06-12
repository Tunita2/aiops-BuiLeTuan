from collections import defaultdict

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from features import normalize_log


OUTCOME_WEIGHTS = {
    "success": 1.0,
    "partial": 0.45,
    "failed": -0.75,
}


def parse_metric_delta(delta: str) -> tuple[float, float]:
    parts = delta.replace("->", "|").split("|")
    if len(parts) != 2:
        return 0.0, 0.0

    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return 0.0, 0.0


def parse_history_action(action: str) -> dict:
    parts = action.split(":")
    name = parts[0]
    values = parts[1:]
    parameter_names = {
        "rollback_service": ["service", "target_version"],
        "increase_pool_size": ["service", "from_value", "to_value"],
        "restart_pod": ["service", "pod_selector"],
        "dns_config_rollback": ["configmap_name", "target_revision"],
        "network_policy_revert": ["policy_name"],
        "page_oncall": ["team"],
    }
    params = {
        key: value
        for key, value in zip(parameter_names.get(name, []), values)
    }
    return {"name": name, "params": params}


def change_direction(value: float, threshold: float = 0.1) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def history_to_features(incident: dict) -> dict:
    log_signatures = [
        normalize_log(signature)
        for signature in incident.get("log_signatures", [])
    ]

    trace_edges = {}
    for trace in incident.get("trace_signatures", []):
        source = trace.get("from")
        target = trace.get("to")
        if not source or not target:
            continue

        edge = f"{source}->{target}"
        error_rate = float(trace.get("error_rate", 0.0))
        deviation = float(trace.get("p99_deviation_ratio", 1.0))
        latency_score = min(max(deviation - 1.0, 0.0) / 4.0, 1.0)
        trace_edges[edge] = {
            "error_rate": error_rate,
            "p99_deviation_ratio": deviation,
            "anomaly_score": 0.7 * error_rate + 0.3 * latency_score,
        }

    metric_changes = {}
    for metric in incident.get("metric_signatures", []):
        service = metric.get("service")
        metric_name = metric.get("metric")
        if not service or not metric_name:
            continue

        before, after = parse_metric_delta(metric.get("delta", ""))
        denominator = max(abs(before), 1e-6)
        relative_change = (after - before) / denominator
        metric_changes[f"{service}.{metric_name}"] = {
            "relative_change": relative_change,
            "direction": change_direction(relative_change),
        }

    return {
        "id": incident.get("id", "unknown"),
        "log_text": " ".join(log_signatures),
        "affected_services": incident.get("affected_services", []),
        "trace_edges": trace_edges,
        "metric_changes": metric_changes,
        "actions_taken": incident.get("actions_taken", []),
        "outcome": incident.get("outcome", "failed"),
        "root_cause_class": incident.get("root_cause_class", "unknown"),
    }


def log_similarity(query_text: str, history_text: str) -> float:
    if not query_text.strip() or not history_text.strip():
        return 0.0

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
    try:
        matrix = vectorizer.fit_transform([query_text, history_text])
    except ValueError:
        return 0.0

    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])


def jaccard_similarity(
    first: list[str] | set[str],
    second: list[str] | set[str],
) -> float:
    first_set = set(first)
    second_set = set(second)
    union = first_set | second_set
    return len(first_set & second_set) / len(union) if union else 0.0


def trace_similarity(query_edges: dict, history_edges: dict) -> float:
    if not query_edges or not history_edges:
        return 0.0

    scores = []
    for history_edge, history_values in history_edges.items():
        history_source, history_target = history_edge.split("->", 1)
        best_score = 0.0

        for query_edge, query_values in query_edges.items():
            query_source, query_target = query_edge.split("->", 1)
            edge_score = 0.0

            if query_edge == history_edge:
                edge_score += 0.65
            else:
                if query_source == history_source:
                    edge_score += 0.15
                if query_target == history_target:
                    edge_score += 0.25

            anomaly_difference = abs(
                float(query_values.get("anomaly_score", 0.0))
                - float(history_values.get("anomaly_score", 0.0))
            )
            edge_score += 0.35 * max(0.0, 1.0 - anomaly_difference)
            best_score = max(best_score, min(edge_score, 1.0))

        scores.append(best_score)

    return sum(scores) / len(scores)


def split_metric_name(name: str) -> tuple[str, str]:
    return tuple(name.split(".", 1)) if "." in name else ("", name)


def metric_similarity(query_metrics: dict, history_metrics: dict) -> float:
    if not query_metrics or not history_metrics:
        return 0.0

    scores = []
    for history_name, history_values in history_metrics.items():
        history_service, history_metric = split_metric_name(history_name)
        best_score = 0.0

        for query_name, query_values in query_metrics.items():
            query_service, query_metric = split_metric_name(query_name)
            score = 0.0

            if query_metric == history_metric:
                score += 0.55
            if query_service == history_service:
                score += 0.20

            query_direction = change_direction(
                float(query_values.get("relative_change", 0.0))
            )
            if query_direction == history_values.get("direction", 0):
                score += 0.25

            best_score = max(best_score, score)

        scores.append(best_score)

    return sum(scores) / len(scores)


def calculate_similarity(query: dict, history: dict) -> dict:
    scores = {
        "log": log_similarity(
            query.get("log_text", ""),
            history.get("log_text", ""),
        ),
        "trace": trace_similarity(
            query.get("trace_edges", {}),
            history.get("trace_edges", {}),
        ),
        "service": jaccard_similarity(
            query.get("affected_services", []),
            history.get("affected_services", []),
        ),
        "metric": metric_similarity(
            query.get("metric_changes", {}),
            history.get("metric_changes", {}),
        ),
    }
    total = (
        0.45 * scores["log"]
        + 0.30 * scores["trace"]
        + 0.15 * scores["service"]
        + 0.10 * scores["metric"]
    )
    return {
        "total": round(total, 4),
        **{name: round(value, 4) for name, value in scores.items()},
    }


def vote_actions(neighbors: list[dict]) -> dict:
    votes = defaultdict(float)
    vote_details = defaultdict(list)

    for neighbor in neighbors:
        similarity = neighbor["similarity"]["total"]
        outcome = neighbor["outcome"]
        outcome_weight = OUTCOME_WEIGHTS.get(outcome, 0.0)
        vote_weight = similarity * outcome_weight

        for raw_action in set(neighbor["actions_taken"]):
            action = parse_history_action(raw_action)
            action_name = action["name"]
            votes[action_name] += vote_weight
            vote_details[action_name].append(
                {
                    "incident_id": neighbor["id"],
                    "similarity": similarity,
                    "outcome": outcome,
                    "outcome_weight": outcome_weight,
                    "vote": round(vote_weight, 4),
                    "historical_params": action["params"],
                }
            )

    ranked_actions = sorted(votes, key=votes.get, reverse=True)
    return {
        "candidate_votes": {
            action: round(votes[action], 4)
            for action in ranked_actions
        },
        "vote_details": {
            action: vote_details[action]
            for action in ranked_actions
        },
        "ranked_actions": ranked_actions,
    }


def retrieve_and_vote(
    query: dict,
    history: list[dict],
    top_k: int = 3,
) -> dict:
    scored_incidents = []

    for incident in history:
        history_features = history_to_features(incident)
        scored_incidents.append(
            {
                **history_features,
                "similarity": calculate_similarity(
                    query,
                    history_features,
                ),
            }
        )

    scored_incidents.sort(
        key=lambda item: item["similarity"]["total"],
        reverse=True,
    )
    neighbors = scored_incidents[:top_k]
    voting = vote_actions(neighbors)

    return {
        "top_3_neighbors": [
            {
                "incident_id": neighbor["id"],
                "root_cause_class": neighbor["root_cause_class"],
                "similarity": neighbor["similarity"],
                "outcome": neighbor["outcome"],
                "actions_taken": neighbor["actions_taken"],
            }
            for neighbor in neighbors
        ],
        "best_similarity": (
            neighbors[0]["similarity"]["total"]
            if neighbors
            else 0.0
        ),
        "candidate_votes": voting["candidate_votes"],
        "vote_details": voting["vote_details"],
        "ranked_actions": voting["ranked_actions"],
    }
