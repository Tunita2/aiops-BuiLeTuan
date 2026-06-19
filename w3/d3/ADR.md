# ADR-008: Dynamic Target Registration and Container Resource Monitoring

## Status
accepted

## Context
During the postmortem analysis of the Cloudflare WAF catastrophic backtracking incident (Gap 1 and Gap 2), two critical architectural gaps in our AIOps observability pipeline were identified:
1. **Lack of Dynamic Target Registry:** Newly spawned service containers (like `reproduction-api-1`) are not automatically discovered or scraped by Prometheus, meaning the AIOps pipeline gets 0 alerts for unregistered services.
2. **Missing CPU/Resource Saturation Signals:** The AIOps pipeline's `AnomalyDetector` only monitors HTTP-level transaction metrics (latency, error rates) and is completely blind to container-level CPU or memory exhaustion.

We need a design decision on how to enable automatic discovery of dynamic targets and how to ingest container-level CPU metrics to detect resource exhaustion events.

## Decision
We will implement Prometheus file-based service discovery (`file_sd`) using a shared JSON target registry updated by a sidecar discovery script, and we will deploy cAdvisor to collect container-level CPU metrics, updating the AIOps `AnomalyDetector` to query and alert on container CPU utilization.

## Alternatives considered
1. **Consul-based Service Discovery (HashiCorp Consul)**
   - *Pros:* Fully dynamic, enterprise-ready service mesh discovery, DNS-based routing.
   - *Cons:* High operational complexity, requires running a separate Consul cluster, introduces a monitoring dependency loop (if Consul goes down, monitoring goes blind, as seen in the Roblox 2021 incident).
   - *Why Rejected:* Too heavyweight for our current mini-platform scale and introduces high architectural overhead.

2. **Static Prometheus Configuration with Port Ranges**
   - *Pros:* Extremely simple to implement, zero additional dependencies.
   - *Cons:* Fragile, fails to capture metadata (like container name, service tags), doesn't scale as ports are allocated dynamically, results in high rate of scrape errors for offline targets.
   - *Why Rejected:* Does not support elastic scaling or modern metadata tagging required by our correlation engine.

3. **Prometheus HTTP Service Discovery (`http_sd`)**
   - *Pros:* Highly standardized HTTP integration, clean JSON schemas.
   - *Cons:* Requires writing and maintaining a new web service that acts as the service registry database.
   - *Why Rejected:* Harder to quickly prototype than shared directory files (`file_sd`) and requires additional service availability monitoring.

## Consequences
- **Positive:**
  - Automated monitoring for all new services; no manual Prometheus reload required when a new API container is deployed.
  - Correlation engine can now map CPU starvation (e.g. pinned WAF API CPU) directly to downstream HTTP timeouts and upstream latency spikes.
  - Keeps operational complexity minimal by using standard Prometheus `file_sd_configs` and lightweight sidecars.
- **Negative:**
  - Increased Prometheus memory footprint due to scraping additional metrics (cAdvisor exports ~200 metrics per container).
  - Risk of disk write latency or concurrency locks if many containers attempt to update the shared target JSON files simultaneously.
- **Risks introduced:**
  - cAdvisor metrics naming might drift across docker/kubernetes environment changes, requiring flexible Prometheus alert queries.
- **What gets locked in:**
  - Target discovery format relies on filesystem sharing between the docker compose helper and the Prometheus container.
