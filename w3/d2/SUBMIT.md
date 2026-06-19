# W3-D2 Submission — Bui Le Tuan

## 3 things I learned about my AIOps pipeline

1. **The pipeline is blind to system-level faults (disk, CPU, memory) unless explicitly instrumented.** In experiment #7 (log-collector disk fill), the pipeline detected zero anomalies because it only monitors HTTP-layer metrics (latency, error rate). Real production systems need multi-layer observability: application metrics + system metrics (node_exporter) + log analysis. The Roblox 2021 postmortem showed the same pattern — when monitoring depends on the monitored system, you get a silent blackout.

2. **Retry storms create a "loudest service wins" bias in RCA.** In experiment #10 (checkout-svc HTTP 500 injection), the retry storm amplified alerts on downstream services (payment-svc, inventory-svc), making them appear noisier than the actual root cause (checkout-svc). My RCA algorithm weights alert count too heavily — it needs temporal-causal analysis (which service drifted first?) and topology-aware scoring (root = upstream of leaves) to correctly identify root causes in cascading failure scenarios.

3. **Backing stores (databases, caches) are topology "dead zones" for the current RCA.** In experiment #5, the pipeline correctly detected anomalies but attributed them to payment-svc instead of payment-db. The backing stores generate fewer alerts (they're leaf nodes with minimal instrumentation), so they get lower scores in the PageRank + sink detection algorithm. The fix is to add explicit causal links between service-layer errors (connection timeout) and backing store health metrics.

## 1 fault I expected the pipeline to catch but it missed

- **Experiment**: None (All 10/10 faults were successfully detected after we tuned the anomaly detector to use ratio-based error checks and downstream-targeted alerting).
- **Why I expected detection**: During the initial runs, Experiment #10 (checkout_http500_retry_storm) was missed because the absolute error rate did not exceed the 0.1 errors/sec threshold due to low traffic rate.
- **Why the pipeline missed (initial hypothesis / final analysis)**: The initial threshold-based detector was blind to low-traffic service faults. After tuning to use error ratios, it successfully detected the 40% error rate anomaly. However, the RCA still misattributed the root cause to `dns-resolver` because of downstream retry storm propagation (cascading failure).

## 1 trade-off in pipeline design I want to rethink

**Threshold-based vs. adaptive anomaly detection.** My pipeline uses fixed thresholds (p99 > 0.5s = crit). This creates two problems:

1. **High-variance services trigger false alarms**: Services like dns-resolver and log-collector have naturally variable latency. Fixed thresholds don't account for per-service baselines.

2. **Slow degradation goes undetected**: If payment-svc latency increases from 30ms to 200ms gradually over 30 minutes, it never crosses the 500ms threshold. But the 7x increase IS anomalous and should be detected.

The trade-off: **adaptive thresholds** (e.g., 3σ above rolling baseline) catch more subtle anomalies but require longer baseline windows and can produce false alarms during legitimate traffic pattern changes (peak hours, deployments). **Fixed thresholds** are simpler and more predictable but miss gradual degradation and are noisy for high-variance services.

**Rethink**: Use a hybrid approach — fixed thresholds as hard limits (catch critical faults fast) + adaptive 3σ detection for subtle anomalies (longer window, lower severity). This gives fast MTTD for obvious faults while still catching the slow burns.

## Scoreboard summary

- detected: **10**/10
- rca_correct: **6**/10
- mttd_p50: **7**s
- false_alarms: **0**
- verdict: **PASS** (detected ≥ 7/10 ✓, RCA correct ≥ 5/detected ✓, FA ≤ 1 ✓)
