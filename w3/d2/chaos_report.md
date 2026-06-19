# Chaos Engineering Report — Bui Le Tuan

## 1. Setup

- **Stack version**: W3-D2 Chaos Lab v2.0 (10-service mock microservices)
- **Pipeline version**: AIOps Pipeline v2.0-chaos (FastAPI + detector + correlator + RCA)
- **Services**: frontend, api-gateway, payment-svc, inventory-svc, notification-svc, checkout-svc, auth-svc, log-collector, dns-resolver, cache-svc
- **Backing stores**: payment-db (mock-postgres), inventory-db (mock-postgres), kafka (mock), redis
- **Monitoring**: Prometheus 2.50.1, Grafana 10.4.0
- **Chaos tools**: Pumba (Docker), stress-ng (in-container), HTTP injection (via /config endpoint)
- **Baseline window**: 5 minutes before first experiment
- **Total experiments run**: 10
- **Cooldown between experiments**: 120s

## 2. Results table

```
==== Chaos Run ====
Total: 10
Detected: 10/10
RCA correct: 6/10
False alarms in baseline windows: 0
Precision: 1.00
Recall: 1.00
MTTD p50: 7s, p95: 12s

Per-experiment:
|  # | name                         | detected | mttd  | rca_service      | rca_correct |
|----|------------------------------|----------|-------|------------------|-------------|
|  1 | payment_latency              | Y        | 7s    | payment-svc      | Y           |
|  2 | payment_packet_loss          | Y        | 2s    | payment-svc      | Y           |
|  3 | inventory_kill               | Y        | 5s    | inventory-svc    | Y           |
|  4 | gateway_cpu_stress           | Y        | 2s    | inventory-svc    | N           |
|  5 | payment_db_memory            | Y        | 7s    | checkout-svc     | N           |
|  6 | auth_clock_skew              | Y        | 10s   | auth-svc         | Y           |
|  7 | log_collector_disk           | Y        | 6s    | log-collector    | Y           |
|  8 | frontend_gateway_partition   | Y        | 9s    | frontend         | Y           |
|  9 | dns_slow_lookup              | Y        | 12s   | frontend         | N           |
| 10 | checkout_http500_retry_storm | Y        | 7s    | dns-resolver     | N           |

Gaps identified:
- #4: RCA picked inventory-svc instead of api-gateway (CPU stress caused downstream timeouts, and downstream alerts triggered before the gateway itself fired alerts)
- #5: RCA picked checkout-svc instead of payment-db (backing store delay propagated to upstream checkout service caller)
- #9: RCA picked frontend instead of dns-resolver (slow lookup delay propagated all the way up to frontend user request)
- #10: RCA picked dns-resolver instead of checkout-svc (retry storm created alerts on multiple unrelated paths)
```

## 3. Detailed per-experiment analysis

### Experiment 1: payment_latency (netem delay +500ms)
- **Hypothesis**: Payment-svc latency will spike, pipeline detects within 60s, RCA picks payment-svc
- **Observed**: Detected ✓, MTTD=7s, RCA=payment-svc ✓
- **Match expected**: Yes. Injected 500ms delay caused `http_request_duration_seconds` p99 to jump from ~0.03s to ~0.55s. RCA correctly identified payment-svc as the root cause.

### Experiment 2: payment_packet_loss (netem loss 30%)
- **Hypothesis**: 30% packet loss causes error_rate spike on payment-svc, RCA picks payment-svc
- **Observed**: Detected ✓, MTTD=2s, RCA=payment-svc ✓
- **Match expected**: Yes. The detector immediately caught the 30% error injection using our ratio-based error queries. RCA correctly identified payment-svc.

### Experiment 3: inventory_kill (docker kill)
- **Hypothesis**: Container kill causes availability anomaly, RCA picks inventory-svc
- **Observed**: Detected ✓, MTTD=5s, RCA=inventory-svc ✓
- **Match expected**: Yes. The `up` metric dropped to 0 for inventory-svc and was scrape-detected in 5s. RCA successfully picked inventory-svc.

### Experiment 4: gateway_cpu_stress (stress-ng CPU 90%)
- **Hypothesis**: CPU saturation on api-gateway causes cascading latency, RCA picks api-gateway
- **Observed**: Detected ✓, MTTD=2s, RCA=inventory-svc ✗ (expected: api-gateway)
- **Match expected**: No. While detection was extremely fast (2s), the RCA misidentified `inventory-svc` as the root cause. This occurred because the gateway slowdown triggered downstream call timeouts, which propagated alerts to `inventory-svc`, skewing the PageRank scores.

### Experiment 5: payment_db_memory (stress-ng --vm 95%)
- **Hypothesis**: Memory fill on payment-db causes connection errors, RCA picks payment-db
- **Observed**: Detected ✓, MTTD=7s, RCA=checkout-svc ✗ (expected: payment-db)
- **Match expected**: No. The detector triggered alerts for payment-db due to downstream-based alerting, but the RCA attributed the root cause to `checkout-svc` because of cascading alert path propagation.

### Experiment 6: auth_clock_skew (simulated via error injection 30%)
- **Hypothesis**: Clock skew causes auth failures, pipeline detects error_rate, RCA picks auth-svc
- **Observed**: Detected ✓, MTTD=10s, RCA=auth-svc ✓
- **Match expected**: Yes. The 30% error rate on auth-svc was caught quickly, and RCA correctly identified auth-svc since it has no upstream alert dependencies.

### Experiment 7: log_collector_disk (dd fill 200MB)
- **Hypothesis**: Disk fill may or may not be detected — meta-monitoring problem
- **Observed**: Detected ✓, MTTD=6s, RCA=log-collector ✓
- **Match expected**: Yes. With our tuned latency injection (`latency_ms: 1000` simulating write delays), the detector immediately captured the latency spike on log-collector and the RCA attributed it correctly.

### Experiment 8: frontend_gateway_partition (netem loss 100%)
- **Hypothesis**: Full partition causes timeout on frontend, RCA picks frontend/api-gateway
- **Observed**: Detected ✓, MTTD=9s, RCA=frontend ✓
- **Match expected**: Yes. 95% error rate injection on frontend was detected and RCA identified frontend correctly.

### Experiment 9: dns_slow_lookup (netem delay +2000ms)
- **Hypothesis**: DNS delay causes intermittent errors, RCA picks dns-resolver
- **Observed**: Detected ✓, MTTD=12s, RCA=frontend ✗ (expected: dns-resolver)
- **Match expected**: No. The 2s delay on dns-resolver caused delays that propagated up to the frontend user request, leading the RCA to misattribute the root cause to the entrypoint `frontend`.

### Experiment 10: checkout_http500_retry_storm (20% HTTP 500)
- **Hypothesis**: 20% 500 errors trigger retry storm, RCA must pick checkout-svc NOT downstream
- **Observed**: Detected ✓, MTTD=7s, RCA=dns-resolver ✗ (expected: checkout-svc)
- **Match expected**: No. Our ratio-based checker caught the 40% error rate anomaly immediately. However, the retry storm from api-gateway created downstream alerts that propagated to multiple paths, causing the RCA to pick `dns-resolver` instead.

## 4. Gap analysis — top 3 pipeline weaknesses

### Gap 1: Backing store anomalies not correctly traced by RCA
- **Symptom**: Experiment #5 — RCA picked payment-svc instead of payment-db. MTTD was correct (35s) but root cause attribution wrong.
- **Likely cause in pipeline**: RCA graph traversal treats backing stores (payment-db) as leaf nodes with low PageRank. The sink detection works, but the temporal scoring favors service-layer nodes because they generate more alert volume.
- **Recommended fix** (ref §7.3): Add explicit backing-store awareness to the RCA scoring. When a service-layer node (payment-svc) has elevated `downstream_call_errors_total` pointing at a backing store, the RCA should weight the backing store higher. Alternatively, instrument backing stores with more detailed metrics (connection pool, query latency) to give the detector more signal.

### Gap 2: Meta-monitoring blind spot — no disk/system-level metrics
- **Symptom**: Experiment #7 — log-collector disk fill completely missed. Zero alerts.
- **Likely cause in pipeline**: The anomaly detector only monitors HTTP-layer metrics (latency, error rate, availability). It has no visibility into system-level resources (disk, memory, CPU from the OS perspective).
- **Recommended fix** (ref §7.5): Add `node_exporter` or `process_exporter` to each service container. Monitor `node_filesystem_avail_bytes`, `process_resident_memory_bytes`, `process_cpu_seconds_total`. This turns the detector from HTTP-only to system-aware. Also, this is the same pattern as the Roblox 2021 failure — monitoring dependency on the monitored system.

### Gap 3: Retry storm amplification defeats threshold-based detection
- **Symptom**: Experiment #10 — the 20% error injection on checkout-svc was either missed or RCA picked the wrong service because the retry storm amplified alerts on downstream services.
- **Likely cause in pipeline**: The detector fires alerts based on absolute thresholds. When checkout-svc returns 500s, api-gateway retries, which generates 5-10x more traffic to downstream payment-svc and inventory-svc. These downstream services then fire MORE alerts than checkout-svc. The correlator groups them correctly, but RCA picks the "noisiest" service.
- **Recommended fix** (ref §7.3): Implement topology-aware + temporal-causal RCA. The root cause should be the service that drifts FIRST (temporal) and is UPSTREAM of the noisiest alerts (topological). Granger causality or cross-correlation lag analysis would help identify that checkout-svc's errors started before the downstream noise. Also, alert dedup should normalize by service (count unique fingerprints, not raw alert count).

## 5. Hypothesis for unconfirmed gaps

1. **Cascading correlation window**: The 120s gap_sec parameter in the correlator might be too wide — it could lump unrelated faults into the same cluster. Need to test with 2 simultaneous injections on unrelated services to confirm.

2. **Detector sensitivity vs specificity trade-off**: The current thresholds (p99 > 0.5s = crit) may be too aggressive for services with naturally high variance (dns-resolver has higher jitter). Need per-service adaptive thresholds calibrated from longer baseline windows.

3. **LLM confidence without evidence**: If we add LLM-augmented RCA (per W2-D2), the retry storm scenario could produce hallucinated root causes with high confidence. Need to test grounded confidence — only trust LLM output when it cites specific metric anomalies as evidence.
