# AIOps Mini-Platform Spec — Bui Le Tuan

## 1. Platform overview
The AIOps Mini-Platform is an automated operations intelligence system designed to monitor a microservices-based application stack (consisting of 10 microservices, such as Frontend, API Gateway, Checkout, Payment, Inventory, and Auth). The platform's scope includes continuous anomaly detection across HTTP metrics, topology-aware alert correlation, and automated root cause analysis (RCA) based on historical incident signatures and dependency graphs. Non-scope includes automated self-healing action execution, container orchestration auto-scaling, and deep network packet capture inspection.

## 2. SLO definition (from W3-D1)
The platform monitors three primary user-facing services with the following specifications:
- **API Gateway Service:**
  - Target SLO: `99.9%` availability over a rolling 30-day window.
  - SLI: `count(status in [2xx, 3xx, 4xx_not_429] AND latency_ms < 500ms) / count(all requests)`
  - Error budget: 20,737 allowed failures per month (equivalent to ~43 minutes of total downtime).
- **Database Service:**
  - Target SLO: `99.9%` query success over a rolling 30-day window.
  - SLI: `count(success=true AND duration_ms < 100ms) / count(all queries)`
  - Error budget: 1,726 allowed failures per month (equivalent to ~43 minutes of total downtime).
- **Frontend Service:**
  - Target SLO: `99.0%` load quality over a rolling 30-day window.
  - SLI: `count(dom_ready_ms < 3000 AND js_error=false AND network_error=false) / count(all page loads)`
  - Error budget: 51,840 allowed failures per month (equivalent to ~432 minutes of total downtime).
- **Burn-rate alert tiers:**
  - Critical: Burn rate > 14.4 (fires page alert, consumes 2% budget in 1 hour).
  - Warning: Burn rate > 6.0 (fires ticket alert, consumes 5% budget in 6 hours).

## 3. Detection + Correlation + RCA stack (from W1+W2)
- **Detector:** 
  - *Algorithm:* Static threshold comparison (latency p99 warning/critical thresholds, ratio-based HTTP error rate warning/critical thresholds, and scrape target ping status).
  - *Input source:* Prometheus instant PromQL queries evaluated every 10 seconds.
  - *Output schema:* JSON list of active alerts with unique IDs, timestamps, service name, metric name, severity, value, and threshold.
- **Correlator:**
  - *Algorithm:* Topology-aware distance-based clustering. Combines alerts occurring within a sliding time window (300 seconds) that are within a defined topological distance (N hops) on the service dependency graph.
  - *Window:* 300 seconds.
  - *Output cluster spec:* List of alert clusters, each containing a `cluster_id`, a list of constituent `alert_ids`, a `services` list, and `start_ts` / `end_ts`.
- **RCA:**
  - *Approach:* Random Walk PageRank on the service dependency graph combined with Temporal Jaccard Similarity matching against historical incident signatures.
  - *Graph source:* Pre-defined static dependency graph (`services.json`) converted into a NetworkX DiGraph.
  - *Output schema:* `{"root_service": "<name>", "confidence": <float 0.0-1.0>, "evidence": "<str>"}`.

## 4. Reliability validation (from W3-D2)
- **Chaos run cadence:** Monthly automated regression test run.
- **Detected/total ratio target:** `100%` detection rate of injected anomalies (10/10 experiments detected).
- **RCA accuracy target:** `70%` correct root service attribution (currently at 60%, i.e., 6/10 experiments correct).
- **Steady-state signal:** Dual verification combining synthetic HTTP probes (`synthetic_probe.py` hitting frontend every 1s) and internal Prometheus scrape targets.

## 5. Operational pattern (from W3-D3)
- **Postmortem template:** Follows the Google SRE blameless incident analysis template, focusing strictly on systemic/process root causes rather than human error. Available at [postmortem.md](file:///d:/Cloude-DevOps/Phase-2/aiops-BuiLeTuan/w3/d3/postmortem.md).
- **On-call rotation:** Tier-based escalations: L1 automated triage via AIOps alerts -> L2 on-call SRE via pager notification -> L3 engineering team escalation if MTTR exceeds 30 minutes.
- **ADR repository:** Architectures and platform design changes are documented in the Nygard format. Active records are located at [ADR.md](file:///d:/Cloude-DevOps/Phase-2/aiops-BuiLeTuan/w3/d3/ADR.md).

## 6. Cost model (from W3-D3)
- **Monthly cost:** $15,000 to $25,000 depending on monitoring scale (compute, storage, and FTE engineering maintenance).
- **Break-even avoided incidents/month:**
  - For mid-tier E-commerce (Scenario 1, $10,000/hr downtime): 1.875 months payback; AIOps pays back if it prevents or speeds up mitigation for at least 2 major incidents per month.
  - For large E-commerce (Scenario 2, $20,000/hr downtime): 0.3125 months payback; AIOps is highly profitable if it reduces MTTR by 40% across 5 incidents.
  - See full implementation details in [cost_model.py](file:///d:/Cloude-DevOps/Phase-2/aiops-BuiLeTuan/w3/d3/cost_model.py).

## 7. Open risks
- **Risk 1 (Cascading Alert Skew):** Slowdowns in edge nodes trigger downstream timeouts, propagating alerts downstream. RCA engine currently misidentifies downstream components as root causes during CPU stress.
  - *Severity:* High
  - *Mitigation plan:* Integrate causal lag analysis (temporal directionality) to bias root cause selection toward the node that drifted first in time.
- **Risk 2 (Lack of Container Auto-Discovery):** Newly deployed container instances are not automatically registered in Prometheus config, causing monitoring blind spots.
  - *Severity:* Medium
  - *Mitigation plan:* Implement file-based service discovery (`file_sd`) as specified in ADR-008.
- **Risk 3 (Absence of Node/Container Saturation Monitoring):** Anomaly detector is blind to host/container CPU and memory usage, failing to distinguish between software bugs and resource exhaustion.
  - *Severity:* Medium
  - *Mitigation plan:* Export container resource metrics via cAdvisor and add threshold checks in `AnomalyDetector` (ADR-008).
