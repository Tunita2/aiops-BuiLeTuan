from collections import defaultdict


OOD_THRESHOLD = 0.25
MIN_AUTO_ACTION_CONFIDENCE = 0.48
TRANSFER_CONFIDENCE_FLOOR = 0.50
MIN_TRANSFER_SIMILARITY = 0.24
MAX_TRANSFER_SIMILARITY = 0.35
PAGE_COMPETITIVE_RATIO = 0.90


DEFAULT_PARAMS = {
    "rollback_service": {
        "target_version": "previous",
    },
    "increase_pool_size": {
        "from_value": "current",
        "to_value": "recommended",
    },
    "restart_pod": {
        "pod_selector": "all",
    },
    "dns_config_rollback": {
        "configmap_name": "affected-config",
        "target_revision": "previous",
    },
    "network_policy_revert": {
        "policy_name": "affected-policy",
    },
    "page_oncall": {
        "team": "platform-team",
    },
}


def find_action_metadata(
    action_name: str,
    actions_catalog: list[dict],
) -> dict:
    for action in actions_catalog:
        if action.get("name") == action_name:
            return action

    return {
        "name": action_name,
        "params": [],
        "cost_min": 0,
        "downtime_min": 0,
        "blast_radius_services": 0,
        "rollback_window_sec": 0,
    }


def positive_votes(
    candidate_votes: dict[str, float],
) -> dict[str, float]:
    # Paging is a fallback, not a zero-cost auto-action candidate.
    return {
        action: max(float(vote), 0.0)
        for action, vote in candidate_votes.items()
        if action != "page_oncall"
    }


def calculate_vote_shares(
    candidate_votes: dict[str, float],
) -> dict[str, float]:
    votes = positive_votes(candidate_votes)
    total = sum(votes.values())

    if total <= 0:
        return {}

    return {
        action: vote / total
        for action, vote in votes.items()
    }


def most_supported_params(
    action_name: str,
    vote_details: dict,
) -> dict:
    parameter_votes: dict[tuple, float] = defaultdict(float)

    for detail in vote_details.get(action_name, []):
        params = detail.get("historical_params", {}) or {}
        vote = max(float(detail.get("vote", 0.0)), 0.0)
        params_key = tuple(sorted(params.items()))
        parameter_votes[params_key] += vote

    if not parameter_votes:
        return {}

    best_key = max(parameter_votes, key=parameter_votes.get)
    return dict(best_key)


def dominant_trace_services(query: dict) -> set[str]:
    """Return services on the single most anomalous trace edge."""
    edges = query.get("anomalous_trace_edges", [])

    if not edges or "->" not in edges[0]:
        return set()

    source, target = edges[0].split("->", 1)
    return {source, target}


def choose_service(
    query: dict,
    historical_params: dict,
    allow_transfer: bool,
) -> str:
    historical_service = historical_params.get("service")
    suspected_root = query.get("suspected_root_service")
    trace_services = dominant_trace_services(query)

    # Keep a historical service only when current trace evidence supports it.
    if historical_service and historical_service in trace_services:
        return historical_service

    # Transfer only when the caller has validated this as a novel-service case.
    if allow_transfer and suspected_root:
        return suspected_root

    if historical_service:
        return historical_service

    return query.get("trigger_service", "unknown")


def build_action_params(
    action_name: str,
    query: dict,
    candidates: dict,
    action_metadata: dict,
    allow_transfer: bool,
) -> dict:
    historical_params = most_supported_params(
        action_name,
        candidates.get("vote_details", {}),
    )

    params = {
        **DEFAULT_PARAMS.get(action_name, {}),
        **historical_params,
    }

    required_params = action_metadata.get("params", [])

    if "service" in required_params:
        params["service"] = choose_service(
            query,
            historical_params,
            allow_transfer,
        )

    if action_name == "rollback_service":
        params["target_version"] = "previous"

    return {
        name: params.get(name, "unknown")
        for name in required_params
    }


def calculate_action_confidence(
    best_similarity: float,
    vote_share: float,
) -> float:
    confidence = (
        0.60 * best_similarity
        + 0.40 * vote_share
    )
    return max(0.0, min(confidence, 1.0))


def action_support_similarity(
    action_name: str,
    candidates: dict,
) -> float:
    details = candidates.get("vote_details", {}).get(
        action_name,
        [],
    )
    return max(
        (
            float(detail.get("similarity", 0.0))
            for detail in details
        ),
        default=0.0,
    )


def can_transfer_action_to_current_root(
    action_name: str,
    query: dict,
    support_similarity: float,
) -> bool:
    suspected_root = query.get("suspected_root_service")
    return (
        action_name in {"restart_pod", "rollback_service"}
        and MIN_TRANSFER_SIMILARITY
        <= support_similarity
        <= MAX_TRANSFER_SIMILARITY
        and suspected_root in dominant_trace_services(query)
    )


def paging_is_competitive(
    candidate_votes: dict[str, float],
) -> bool:
    page_vote = max(
        float(candidate_votes.get("page_oncall", 0.0)),
        0.0,
    )
    auto_votes = positive_votes(candidate_votes)
    best_auto_vote = max(auto_votes.values(), default=0.0)

    return (
        page_vote > 0
        and best_auto_vote > 0
        and page_vote >= best_auto_vote * PAGE_COMPETITIVE_RATIO
    )


def calculate_utility(
    confidence: float,
    metadata: dict,
) -> float:
    cost = float(metadata.get("cost_min", 0))
    downtime = float(metadata.get("downtime_min", 0))
    blast_radius = float(
        metadata.get("blast_radius_services", 0)
    )

    expected_benefit = confidence * 100.0
    risk_penalty = (
        cost * 0.8
        + downtime * 2.0
        + blast_radius * 8.0
    )
    return expected_benefit - risk_penalty


def passes_blast_radius_gate(
    confidence: float,
    metadata: dict,
) -> tuple[bool, str]:
    blast_radius = int(
        metadata.get("blast_radius_services", 0)
    )

    if blast_radius >= 4 and confidence < 0.85:
        return False, (
            "rejected: blast radius >= 4 requires "
            "confidence >= 0.85"
        )

    if blast_radius >= 3 and confidence < 0.75:
        return False, (
            "rejected: blast radius >= 3 requires "
            "confidence >= 0.75"
        )

    if confidence < MIN_AUTO_ACTION_CONFIDENCE:
        return False, (
            "rejected: confidence below auto-action threshold"
        )

    return True, "passed"


def apply_trace_consistency_gate(
    gate_passed: bool,
    gate_reason: str,
    params: dict,
    query: dict,
) -> tuple[bool, str]:
    action_service = params.get("service")
    trace_services = dominant_trace_services(query)

    if (
        gate_passed
        and action_service
        and trace_services
        and action_service not in trace_services
    ):
        return False, (
            f"rejected: action service {action_service} conflicts "
            f"with dominant trace services {sorted(trace_services)}"
        )

    return gate_passed, gate_reason


def page_oncall_decision(
    reason: str,
    query: dict,
    candidates: dict,
    confidence: float = 0.0,
    evaluated_candidates: list[dict] | None = None,
) -> dict:
    return {
        "selected_action": "page_oncall",
        "params": {
            "team": "platform-team",
        },
        "confidence": round(confidence, 4),
        "top_3_neighbors": candidates.get(
            "top_3_neighbors",
            [],
        ),
        "consensus_score": round(
            max(
                calculate_vote_shares(
                    candidates.get("candidate_votes", {})
                ).values(),
                default=0.0,
            ),
            4,
        ),
        "blast_radius_check": "escalated",
        "selected_action_meta": {
            "blast_radius_services": 0,
        },
        "evidence": {
            "reason": reason,
            "suspected_root_service": query.get(
                "suspected_root_service"
            ),
            "dominant_trace_services": sorted(
                dominant_trace_services(query)
            ),
            "best_similarity": candidates.get(
                "best_similarity",
                0.0,
            ),
            "candidate_votes": candidates.get(
                "candidate_votes",
                {},
            ),
            "evaluated_candidates": evaluated_candidates or [],
        },
    }


def select_action(
    query: dict,
    candidates: dict,
    actions_catalog: list[dict],
) -> dict:
    best_similarity = float(
        candidates.get("best_similarity", 0.0)
    )
    candidate_votes = candidates.get(
        "candidate_votes",
        {},
    )

    if best_similarity < OOD_THRESHOLD:
        return page_oncall_decision(
            reason=(
                f"OOD: best similarity {best_similarity:.4f} "
                f"is below threshold {OOD_THRESHOLD:.2f}"
            ),
            query=query,
            candidates=candidates,
            confidence=1.0 - best_similarity,
        )

    vote_shares = calculate_vote_shares(candidate_votes)

    if not vote_shares:
        return page_oncall_decision(
            reason="No positive auto-action votes",
            query=query,
            candidates=candidates,
        )

    if paging_is_competitive(candidate_votes):
        return page_oncall_decision(
            reason=(
                "Historical paging evidence is competitive with "
                "the best auto-action vote"
            ),
            query=query,
            candidates=candidates,
        )

    evaluated_candidates = []

    for action_name, vote_share in vote_shares.items():
        metadata = find_action_metadata(
            action_name,
            actions_catalog,
        )
        support_similarity = action_support_similarity(
            action_name,
            candidates,
        )
        transferred = can_transfer_action_to_current_root(
            action_name,
            query,
            support_similarity,
        )
        params = build_action_params(
            action_name,
            query,
            candidates,
            metadata,
            transferred,
        )
        confidence = calculate_action_confidence(
            best_similarity,
            vote_share,
        )

        if transferred:
            confidence = max(
                confidence,
                TRANSFER_CONFIDENCE_FLOOR,
            )

        utility = calculate_utility(
            confidence,
            metadata,
        )
        gate_passed, gate_reason = passes_blast_radius_gate(
            confidence,
            metadata,
        )
        gate_passed, gate_reason = apply_trace_consistency_gate(
            gate_passed,
            gate_reason,
            params,
            query,
        )

        evaluated_candidates.append(
            {
                "action": action_name,
                "params": params,
                "raw_vote": round(
                    candidate_votes.get(action_name, 0.0),
                    4,
                ),
                "vote_share": round(vote_share, 4),
                "confidence": round(confidence, 4),
                "confidence_floor_applied": transferred,
                "action_support_similarity": round(
                    support_similarity,
                    4,
                ),
                "utility": round(utility, 4),
                "cost_min": metadata.get("cost_min", 0),
                "downtime_min": metadata.get(
                    "downtime_min",
                    0,
                ),
                "blast_radius_services": metadata.get(
                    "blast_radius_services",
                    0,
                ),
                "gate_passed": gate_passed,
                "gate_reason": gate_reason,
            }
        )

    eligible = [
        candidate
        for candidate in evaluated_candidates
        if candidate["gate_passed"]
    ]
    eligible.sort(
        key=lambda candidate: candidate["utility"],
        reverse=True,
    )

    if not eligible:
        best_candidate_confidence = max(
            (
                candidate["confidence"]
                for candidate in evaluated_candidates
            ),
            default=0.0,
        )
        return page_oncall_decision(
            reason="All auto-actions rejected by safety gates",
            query=query,
            candidates=candidates,
            confidence=best_candidate_confidence,
            evaluated_candidates=evaluated_candidates,
        )

    selected = eligible[0]
    selected_metadata = find_action_metadata(
        selected["action"],
        actions_catalog,
    )

    return {
        "selected_action": selected["action"],
        "params": selected["params"],
        "confidence": selected["confidence"],
        "top_3_neighbors": candidates.get(
            "top_3_neighbors",
            [],
        ),
        "consensus_score": round(
            vote_shares.get(selected["action"], 0.0),
            4,
        ),
        "blast_radius_check": selected["gate_reason"],
        "selected_action_meta": selected_metadata,
        "evidence": {
            "suspected_root_service": query.get(
                "suspected_root_service"
            ),
            "dominant_trace_services": sorted(
                dominant_trace_services(query)
            ),
            "affected_services": query.get(
                "affected_services",
                [],
            ),
            "best_similarity": best_similarity,
            "candidate_votes": candidate_votes,
            "vote_shares": {
                action: round(share, 4)
                for action, share in vote_shares.items()
            },
            "evaluated_candidates": evaluated_candidates,
            "selection_reason": (
                "Highest utility among actions that passed "
                "confidence, trace-consistency, and blast-radius gates"
            ),
        },
    }
