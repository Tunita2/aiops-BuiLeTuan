import re
from collections import Counter, defaultdict
from statistics import mean


IMPORTANT_LEVELS = {"WARN", "ERROR"}


def normalize_log(message: str) -> str:
    """Normalize volatile values so raw logs resemble history templates."""
    text = message.lower()
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", text)
    text = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_log_features(logs: list[dict]) -> dict:
    templates = Counter()
    service_counts = defaultdict(int)

    for log in logs:
        level = str(log.get("level", "")).upper()
        if level not in IMPORTANT_LEVELS:
            continue

        service = log.get("svc", "unknown")
        template = normalize_log(log.get("msg", ""))
        if not template:
            continue

        templates[template] += 1
        service_counts[service] += 1

    # Cap repeated templates so log spam does not dominate TF-IDF.
    log_documents = []
    for template, count in templates.items():
        log_documents.extend([template] * min(count, 3))

    return {
        "log_text": " ".join(log_documents),
        "log_templates": dict(templates),
        "log_counts_by_service": dict(service_counts),
    }


def extract_trace_features(traces: list[dict]) -> dict:
    edges = defaultdict(
        lambda: {
            "count": 0,
            "error_count": 0,
            "weighted_p99": 0.0,
        }
    )

    for trace in traces:
        source = trace.get("from")
        target = trace.get("to")
        if not source or not target:
            continue

        key = f"{source}->{target}"
        count = max(int(trace.get("count", 0)), 0)
        errors = max(int(trace.get("error_count", 0)), 0)
        p99 = max(float(trace.get("p99_ms", 0)), 0.0)

        edges[key]["count"] += count
        edges[key]["error_count"] += errors
        edges[key]["weighted_p99"] += p99 * max(count, 1)

    output = {}
    for edge, values in edges.items():
        count = values["count"]
        error_rate = values["error_count"] / max(count, 1)
        average_p99 = values["weighted_p99"] / max(count, 1)
        latency_score = min(average_p99 / 5000.0, 1.0)
        anomaly_score = 0.7 * error_rate + 0.3 * latency_score

        output[edge] = {
            "count": count,
            "error_rate": round(error_rate, 4),
            "p99_ms": round(average_p99, 2),
            "anomaly_score": round(anomaly_score, 4),
        }

    return {
        "trace_edges": output,
        "anomalous_trace_edges": sorted(
            output,
            key=lambda edge: output[edge]["anomaly_score"],
            reverse=True,
        )[:5],
    }


def safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def extract_metric_features(metrics_window: dict) -> dict:
    changes = {}

    for metric_name, series in metrics_window.get("samples", {}).items():
        values = []
        for sample in series:
            if not isinstance(sample, list) or len(sample) < 2:
                continue
            try:
                values.append(float(sample[1]))
            except (TypeError, ValueError):
                continue

        if len(values) < 6:
            continue

        # Use sample order because some declared window timestamps are noisy.
        segment_size = max(int(len(values) * 0.3), 1)
        baseline = safe_mean(values[:segment_size])
        current = safe_mean(values[-segment_size:])
        denominator = max(abs(baseline), 1e-6)
        ratio = current / denominator
        relative_change = (current - baseline) / denominator

        changes[metric_name] = {
            "baseline": round(baseline, 4),
            "current": round(current, 4),
            "ratio": round(ratio, 4),
            "relative_change": round(relative_change, 4),
            "anomaly_score": round(
                min(abs(relative_change), 5.0) / 5.0,
                4,
            ),
        }

    return {
        "metric_changes": changes,
        "anomalous_metrics": sorted(
            changes,
            key=lambda name: changes[name]["anomaly_score"],
            reverse=True,
        )[:5],
    }


def split_edge(edge: str) -> tuple[str, str]:
    return tuple(edge.split("->", maxsplit=1))


def service_from_metric(metric_name: str) -> str:
    return metric_name.split(".", maxsplit=1)[0]


def rank_services(
    trigger_service: str,
    log_features: dict,
    trace_features: dict,
    metric_features: dict,
) -> dict:
    scores = defaultdict(float)
    reasons = defaultdict(list)

    scores[trigger_service] += 0.25
    reasons[trigger_service].append("trigger_alert")

    for service, count in log_features["log_counts_by_service"].items():
        score = min(count / 20.0, 1.0) * 0.35
        scores[service] += score
        reasons[service].append(f"important_logs={count}")

    for edge, values in trace_features["trace_edges"].items():
        source, target = split_edge(edge)
        anomaly = values["anomaly_score"]
        scores[source] += anomaly * 0.20
        scores[target] += anomaly * 0.55

        if anomaly >= 0.2:
            reasons[source].append(f"anomalous_trace_source={edge}")
            reasons[target].append(f"anomalous_trace_target={edge}")

    for metric_name, values in metric_features["metric_changes"].items():
        service = service_from_metric(metric_name)
        anomaly = values["anomaly_score"]
        scores[service] += anomaly * 0.35

        if anomaly >= 0.2:
            reasons[service].append(f"anomalous_metric={metric_name}")

    ranking = sorted(scores, key=scores.get, reverse=True)
    return {
        "affected_services": ranking,
        "service_scores": {
            service: round(scores[service], 4)
            for service in ranking
        },
        "service_reasons": dict(reasons),
        "suspected_root_service": ranking[0] if ranking else trigger_service,
    }


def extract_features(incident: dict) -> dict:
    trigger_service = incident.get("trigger_alert", {}).get(
        "service",
        "unknown",
    )
    log_features = extract_log_features(incident.get("logs", []))
    trace_features = extract_trace_features(incident.get("traces", []))
    metric_features = extract_metric_features(
        incident.get("metrics_window", {})
    )
    service_features = rank_services(
        trigger_service,
        log_features,
        trace_features,
        metric_features,
    )

    return {
        "trigger_service": trigger_service,
        "log_text": log_features["log_text"],
        "log_templates": log_features["log_templates"],
        "log_counts_by_service": log_features["log_counts_by_service"],
        "trace_edges": trace_features["trace_edges"],
        "anomalous_trace_edges": trace_features["anomalous_trace_edges"],
        "metric_changes": metric_features["metric_changes"],
        "anomalous_metrics": metric_features["anomalous_metrics"],
        "affected_services": service_features["affected_services"],
        "service_scores": service_features["service_scores"],
        "service_reasons": service_features["service_reasons"],
        "suspected_root_service": (
            service_features["suspected_root_service"]
        ),
    }
