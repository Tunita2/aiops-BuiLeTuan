# Postmortem: Cloudflare WAF Regex Catastrophic Backtracking (2026-06-19)

## Summary
During the deployment of a new web application firewall (WAF) rule, an incoming HTTP request containing a crafted query triggered a catastrophic backtracking condition within the regex validation middleware. The regex engine consumed 100% CPU on the API Gateway edge instance, rendering the primary user-facing API endpoint completely unresponsive. The incident lasted approximately 27 minutes until the WAF middleware configuration was deactivated.

## Impact
- **Users affected:** ~82% drop in edge traffic and API requests during the outage window.
- **Services affected:** Primary API Gateway (`reproduction-api-1` container).
- **Revenue/SLA impact:** Severe breach of availability SLO (target 99.9%); estimated direct revenue loss of $15,000 based on transaction bounce rates.
- **Duration:** 2026-06-19 09:42:02 UTC → 09:42:29 UTC (outage simulated for ~27 seconds).

## Timeline (UTC)

| UTC | Event |
|-----|-------|
| 2026-06-19 09:41:42 | Periodic container healthcheck query executed on background MLFlow services. |
| 2026-06-19 09:42:02 | An HTTP request containing a query string with a single equals sign (`q=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`) was routed to the WAF. |
| 2026-06-19 09:42:03 | The WAF regex engine began executing the regular expression match, triggering exponential backtracking. CPU pinned to 100%. |
| 2026-06-19 09:42:12 | On-call SRE initiated diagnostic checks; executed `ls` in the `reproduction-api-1` container to check container responsiveness. |
| 2026-06-19 09:42:13 | SRE observed extremely high shell command execution latency (~1 second for basic `ls` command), signaling CPU starvation. |
| 2026-06-19 09:42:14 | The client-side HTTP request timed out after 10,000 milliseconds with 0 bytes returned. |
| 2026-06-19 09:42:16 | SRE executed diagnostic `whoami` and `pwd` inside the container, confirming continued process degradation. |
| 2026-06-19 09:42:17 | SRE checked system hostnames, disk usage (`df -h`), and logged "Outage simulated" to verify state. |
| 2026-06-19 09:42:20 | Normal background database health checks continued to execute, but edge API requests remained blocked. |
| 2026-06-19 09:42:29 | The SRE disabled the WAF rule configuration environment variable, and the container was recycled to restore CPU capacity. |

## Root cause
The deployment configuration utilized an un-anchored regular expression containing nested, overlapping repetition quantifiers `(?:(?:"|\d|.*)+(?:.*=.*))` that is highly vulnerable to catastrophic backtracking when evaluated against non-matching strings containing a single equals sign.

## Contributing factors
1. The pre-deployment linting and integration testing pipelines did not include a Static Regex vulnerability analyzer (such as a ReDoS checker).
2. The WAF configuration update was rolled out globally and instantly without a localized canary progression (e.g., 1% -> 10% -> 100%).

## Detection
- **How was it detected?** Detected via manual user reports and client-side HTTP timeout alerts (HTTP 504 / gateway timeout).
- **MTTD:** ~12 seconds from trigger to manual diagnostic observation.
- **Pipeline gaps observed during reproduction:**
  - **Gap 1 (No Automatic Service Discovery):** The newly deployed `reproduction-api-1` container was not automatically registered as a Prometheus scrape target. As a result, HTTP metrics were not scraped, and the AIOps pipeline's `/alerts` endpoint returned 0 alerts.
  - **Gap 2 (No CPU Saturation Ingestion):** The AIOps pipeline's `AnomalyDetector` only monitors HTTP level application metrics (latency, error rates). It lacks metrics ingestion from container-level resources (e.g., `container_cpu_usage_seconds_total` from cAdvisor or Node Exporter), meaning CPU starvation failures cannot be correlated.

## Response
- **First responder action:** Executed diagnostics (`ls`, `whoami`, `df`) inside the container and reviewed environment variables.
- **Time to mitigate:** 27 seconds to isolate and disable the evil regex rule.
- **Time to fully resolve:** 27 seconds (immediate recovery upon container recreation and rule deactivation).

## Action items

| # | Action | Owner | Type | ETA |
|---|--------|-------|------|-----|
| 1 | Integrate static ReDoS analyzer checks into the CI/CD pre-commit hooks for all regex pattern deployments. | SRE Team | preventive | 2026-06-26 |
| 2 | Implement dynamic service discovery (e.g., Consul or Prometheus file_sd) to automatically scrape new microservices. | Platform Team | preventive | 2026-06-30 |
| 3 | Update AIOps `AnomalyDetector` to query and alert on container-level CPU saturation metrics from cAdvisor. | AIOps Team | detective | 2026-06-28 |
