**W3-D3: Outage Reproduction, Postmortem, ADR, Cost Model**

15 min read3032 words

**Outage Reproduction, Postmortem, ADR, Cost Model**

**1. Định nghĩa**

|**Khái niệm**|**Định nghĩa**|
| :- | :- |
|**Postmortem**|Document phân tích incident sau khi đã resolve: timeline, root cause, contributing factors, action items|
|**Blameless principle**|Postmortem focus on systemic cause, không nhằm vào cá nhân. Lý do: blame culture → người báo lỗi ít → bug ẩn lâu hơn|
|**ADR (Architecture Decision Record)**|1-page document ghi 1 quyết định kiến trúc: context, decision, alternatives, consequences|
|**MTTR / MTTD / MTBF**|Mean Time To Recover / Detect / Between Failures|
|**Error budget**|(1 - SLO) × total events; quota cho phép fail|

Sources:

- Postmortem culture: Beyer et al., *SRE Book* Ch 15. [sre.google/sre-book/postmortem-culture](https://sre.google/sre-book/postmortem-culture/)
- ADR origin: Michael Nygard, “Documenting Architecture Decisions” (2011). [cognitect.com/blog/2011/11/15/documenting-architecture-decisions](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
-----
**2. Postmortem template — fields chuẩn Google SRE**

**# Postmortem: <short incident name>**

**\*\*Status:\*\*** complete | draft  

**\*\*Date:\*\*** YYYY-MM-DD  

**\*\*Authors:\*\*** <names>  

**\*\*Severity:\*\*** SEV1 | SEV2 | SEV3  

**\*\*Duration:\*\*** <minutes> (start UTC → end UTC)

**## Summary**

<2-4 sentences: what happened, who was affected, how it was fixed>

**## Impact**

\- Users affected: <number / %>

\- Revenue impact: $<estimate>

\- SLO budget consumed: <%>

\- External communication: <status page updates, blog post>

**## Timeline (UTC)**

| Time | Event |

\|------|-------|

| HH:MM | Trigger event (deploy, config push, traffic surge) |

| HH:MM | First user-visible symptom |

| HH:MM | First page fired |

| HH:MM | On-call ack |

| HH:MM | Root cause identified |

| HH:MM | Mitigation applied |

| HH:MM | Full recovery |

**## Root cause**

<technical explanation: what broke, why, why detection delayed>

**## Contributing factors**

\- <factor 1: e.g., insufficient canary, missing alert>

\- <factor 2>

**## Detection**

\- How was the incident detected? (user report, alert, dashboard)

\- Could it have been detected earlier?

**## Response**

\- What went well

\- What went poorly

\- Where we got lucky

**## Action items**

| Item | Owner | Due | Priority |

\|------|-------|-----|----------|

| <action 1> | <name> | <date> | P0/P1/P2 |

**2.1 Blameless wording**

|**Blame-y (avoid)**|**Blameless (use)**|
| :- | :- |
|“Alice pushed bad config”|“Config push pipeline allowed invalid YAML through”|
|“On-call was slow to respond”|“Alert routing didn’t reach on-call’s primary device”|
|“Engineer forgot to test”|“Pre-merge test suite didn’t cover this scenario”|

Focus: **system + process**, không phải **người**.

-----
**3. Root cause analysis: 5 Whys vs Causal Tree**

**3.1 5 Whys (Toyota, 1950s)**

Linear chain, lặp “why?” 5 lần:

Symptom: API trả 500 lúc 02:14 UTC

Why? → DB connection pool exhausted

Why? → 1 query lock cả pool, block 30s

Why? → Query missing index, full table scan

Why? → Index removed bởi migration tuần trước

Why? → Migration review không catch performance regression

Strength: đơn giản, ai cũng dùng được.\
Limit: assume nguyên nhân tuyến tính. Outage thật thường nhiều nhánh.

**3.2 Causal Tree**

Multiple branches, mỗi branch có thể có cause riêng:

`                     `Outage 24h11m (GitHub 2018-10-21)

`                              `│

`            `┌─────────────────┼─────────────────┐

`            `│                 │                 │

`       `Network blip       Orchestrator      Consistency-first

`       `43 seconds         failover triggered  recovery policy

`            `│                 │                 │

`       `Routine optical    Quorum logic      Engineering decision

`       `maintenance        deemed primary     (no data loss > faster)

`       `(BGP convergence   unreachable

`        `slow)

Dùng khi:

- Outage có > 1 failure mode đồng thời (Roblox: Consul streaming + BoltDB)
- Có architectural decision contribute (consistency vs availability trade-off)

Source: Allspaw & Robbins, *Web Operations*, O’Reilly 2010, Ch 10. Causal tree analysis pattern.

-----
**4. Failure mode catalog — 6 patterns với real incident citation**

|**Pattern**|**Mechanism**|**Real incident**|
| :- | :- | :- |
|**Cascading failure**|A fail → retry storm → B saturate → C saturate|AWS Lambda 2018, DynamoDB 2015|
|**Split-brain**|Network partition → 2 nodes nghĩ mình là primary → divergent state|GitHub MySQL 2018-10-21|
|**Catastrophic backtracking**|Regex / parser exponential time on adversarial input|Cloudflare 2019-07-02|
|**Capacity exhaustion at boundary**|File descriptor, conn pool, thread pool full|LinkedIn 2017 conn pool, Cloudflare 2022 fd leak|
|**Monitoring dependency loop**|AIOps stack depends on monitored service → service fail → monitoring blind|Roblox 2021-10-28 (Consul)|
|**Operator action without guardrail**|Typo / wrong scope command takes down prod|AWS S3 2017-02-28, GitLab 2017-01-31 (db delete)|

**4.1 Pattern detail: Cascading failure**

User traffic

`    `↓

[Service A] ──fail──→ retries with backoff

`    `↓                       ↓

[Service B] ←──retry storm (10×)──

`    `↓ (CPU saturated by retry handling)

[Service C] ←──fails to get response from B

`    `↓

Whole system degraded

**Detection trap:** alert count cao nhất ở C (downstream), không phải A (root). Naive RCA pick C → wrong. Cần topology-aware + causal-lag analysis.

**4.2 Pattern detail: Split-brain (GitHub 2018)**

GitHub 2018-10-21, 22:52 UTC. Routine optical equipment maintenance gây 43-second connectivity loss giữa US East coast hub và US East data center. Orchestrator (MySQL failover tool) trên US West thấy East unreachable → triggered failover, promote West replica thành primary. 43s sau East reconnect → 2 primary → divergent writes.

Recovery 24h 11m vì GitHub chose consistency over speed: replay binary log từ East to West thay vì serve possibly-stale data.

Source: [github.blog/2018-10-30-oct21-post-incident-analysis](https://blog.github.com/2018-10-30-oct21-post-incident-analysis)

**4.3 Pattern detail: Catastrophic backtracking (Cloudflare 2019)**

2019-07-02, 13:42 UTC. Cloudflare deploy WAF rule global cùng lúc. Rule chứa regex (?:(?:\"|'|\]|\}|\\|\d|(?:nan|infinity|true|false|null|undefined|symbol|math)|\|-|+)+[)]*;?((?:\s|-|~|!|{}||||+)*.*(?:.*=.\*)))`.

Khi gặp input như xxxxx=xxxxxx, regex engine try exponential combinations of .\* group → CPU 100% trên mọi edge worldwide. 27 phút outage, traffic drop 82%.

Counter: tested regex against ReDoS (catastrophic backtracking detector) trước deploy; canary rollout 1% → 10% → 100% thay vì global atomic.

Source: [blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019](https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019/)

**4.4 Pattern detail: Monitoring loop (Roblox 2021)**

2021-10-28 → 10-31, 73 hours. Roblox enabled Consul streaming feature dưới production load. Streaming sử dụng ít Go channel hơn long-polling → contention dưới high read+write concurrent → blocking writes. BoltDB freelist algorithm O(n²) at scale → write latency spike.

Critical detail: Roblox monitoring stack depend on Consul cho service discovery. Consul slow → monitoring queries timeout → on-call had no visibility for first ~12 hours. Diagnosis 60+ hours vì 2 unrelated issues (streaming contention + BoltDB) overlap.

Source: [about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021](https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021)

-----
**5. Outage catalog — pick 1 to reproduce**

|**#**|**Incident**|**Date**|**Duration**|**Failure mode**|**Reproduce difficulty**|**Postmortem URL**|
| :- | :- | :- | :- | :- | :- | :- |
|1|AWS S3 us-east-1|2017-02-28|~4h|Operator typo, insufficient blast radius|Easy|<https://aws.amazon.com/message/41926/>|
|2|GitHub MySQL split-brain|2018-10-21|24h 11m|Network partition + Orchestrator failover|Hard|<https://github.blog/2018-10-30-oct21-post-incident-analysis>|
|3|Cloudflare WAF regex|2019-07-02|27 min|Catastrophic backtracking + global deploy|Medium|<https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019>|
|4|Roblox Consul + BoltDB|2021-10-28|73h|Streaming contention + freelist + monitoring loop|Hard|<https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021>|
|5|Slack Jan 4 2022|2022-01-04|~6h|Provisioning system overload post-holiday|Medium|<https://slack.engineering/slacks-incident-on-2-22-22/>|

danluu/post-mortems archive (cộng đồng curate): [github.com/danluu/post-mortems](https://github.com/danluu/post-mortems)

-----
**6. Reproduction patterns**

Mỗi outage có 1 minimal docker-compose có thể trigger same failure mode.

**6.1 AWS S3 2017 reproduction (operator typo)**

Mô phỏng: script delete server từ “subsystem A” (billing) bằng input mismatch lấy mất server “subsystem B” (index) + “subsystem C” (placement).

*# docker-compose.yml*

services:

`  `billing:

`    `image: alpine

`    `command: sleep infinity

`  `index:

`    `image: alpine

`    `command: sleep infinity

`  `placement:

`    `image: alpine

`    `command: sleep infinity

*# bad-command.sh*

docker compose stop --remove-orphans  # ← typo: should have been "stop billing"

Result: chạy bash → 3 service cùng down → index + placement down nghĩa là không serve được object metadata + không decide được object location.

Postmortem để viết: tại sao 1 command có thể wipe 3 service? Guardrail nào missing?

**6.2 Cloudflare 2019 reproduction (regex CPU)**

import re**,** time

EVIL **=** r'(?:(?:\"|\d|.\*)+(?:.\*=.\*))'  *# simplified evil regex*

INPUT **=** "x=" **+** "x" **\*** 30

t0 **=** time**.**time()

re**.**match(EVIL, INPUT)

print(f"matched in {time**.**time()**-**t0:.2f}s — should be < 0.01s")

*# Real run: 8-15 seconds on commodity hardware → CPU pegged*

Wrap into HTTP middleware → server unresponsive trong vòng giây. AIOps pipeline có catch không?

**6.3 GitHub 2018 reproduction (simplified split-brain)**

services:

`  `mysql-primary:

`    `image: mysql:8

`    `networks: [east]

`  `mysql-replica:

`    `image: mysql:8

`    `networks: [west]

`  `orchestrator:

`    `image: openark/orchestrator:latest

`    `networks: [east, west]

networks:

`  `east: {}

`  `west: {}

*# Trigger:*

*# docker network disconnect east orchestrator   # 43s*

*# (orchestrator from west sees east unreachable, promotes replica)*

*# docker network connect east orchestrator*

*# Both DB now think they're primary → write conflicts*

-----
**7. Architecture Decision Record (ADR)**

**7.1 Format Nygard (2011)**

**# ADR-NNN: <short title of decision>**

**## Status**

Proposed | Accepted | Deprecated | Superseded by ADR-XXX

**## Context**

<situation prompting the decision: forces at play, constraints>

**## Decision**

<the change we're making>

**## Alternatives considered**

\- Alternative A — pros, cons, why rejected

\- Alternative B — pros, cons, why rejected

**## Consequences**

\- Positive consequence 1

\- Negative consequence 1 (trade-off accepted)

\- Risk 1, mitigation

**7.2 Ví dụ ADR cho AIOps platform**

**# ADR-007: Use topology-aware RCA over count-based ranking**

**## Status**

Accepted

**## Context**

RCA cần pick root service từ N service đang fire alert. Count-based ranking

(service nhiều alert nhất = root) fails on cascading failure: downstream

service retry → fires more alerts than upstream root.

**## Decision**

RCA combine 3 signals:

1\. Topology distance from edge (upstream-bias)

2\. First-drift time (causal lag analysis via Granger causality)

3\. Alert volume (tiebreaker only)

**## Alternatives considered**

\- Count-only ranking — simple, fast, BUT fails retry storm. Rejected.

\- LLM-only RCA — flexible, BUT hallucinate confident-wrong root. Rejected as primary.

\- Graph PageRank only — captures topology BUT not temporal causality. Rejected as standalone.

**## Consequences**

\+ Catches cascading patterns missed by count-only (verified vs Roblox-style scenario)

\+ Composable: each signal degrades gracefully if data missing

− Higher compute cost (Granger causality O(n × lag\_window))

− Requires topology graph kept up-to-date — adds operational burden

\- Risk: signal weights need tuning per environment, not auto.

-----
**8. Cost model — break-even cho AIOps platform**

**8.1 Cost side**

|**Component**|**Đơn vị**|**Order of magnitude**|
| :- | :- | :- |
|Metric ingestion|$/series/month|$0.0001 (Prometheus self-host) — $0.30 (Datadog)|
|Log ingestion|$/GB|$0.30 (S3 + Athena) — $2.50 (Datadog) — $5 (Splunk)|
|Trace ingestion|$/million spans|$0.50 (Tempo) — $2 (Datadog APM)|
|Model inference compute|$/hour|$0.05 (CPU c6i.large) — $3.06 (GPU g5.xlarge)|
|Storage hot/warm/cold|$/GB/month|$0.023 (S3) — $0.10 (gp3) — $0.30 (Prometheus local)|
|SRE/AIOps engineer|$/year|$120k–$250k loaded cost|
|On-call rotation overhead|hours/engineer/month|40–80h ≈ $5k–$15k/month opportunity cost|

**8.2 Value side**

\text{value\_per\_year} = \text{MTTR\_reduction\_hours} \times \text{incident\_per\_year} \times \text{downtime\_cost\_per\_hour}

Downtime cost per hour:

|**Business type**|**Order of magnitude**|
| :- | :- |
|E-commerce mid-tier|$5k–$50k/hour|
|Large e-commerce (Amazon-scale)|$200k+/hour|
|Financial trading|$1M+/hour|
|Internal SaaS|$500–$5k/hour|
|Streaming (Netflix, etc.)|$50k–$500k/hour|

Source: ITIC 2024 Hourly Cost of Downtime Survey, Gartner 2014 (often-cited $5,600/min baseline).

**8.3 Break-even formula**

def is\_worth\_it(

`    `num\_services: int,

`    `incidents\_per\_month: int,

`    `avg\_incident\_duration\_hours: float,

`    `downtime\_cost\_per\_hour: float,

`    `expected\_mttr\_reduction\_pct: float **=** 0.4,

`    `aiops\_monthly\_cost: float **=** 15\_000,

) **->** dict:

`    `monthly\_downtime\_hours **=** incidents\_per\_month **\*** avg\_incident\_duration\_hours

`    `monthly\_value **=** (

`        `monthly\_downtime\_hours

`        `**\*** expected\_mttr\_reduction\_pct

`        `**\*** downtime\_cost\_per\_hour

`    `)

`    `roi **=** monthly\_value **/** aiops\_monthly\_cost

`    `payback\_months **=** aiops\_monthly\_cost **/** monthly\_value if monthly\_value **>** 0 else float("inf")

`    `return {

`        `"monthly\_value": monthly\_value,

`        `"monthly\_cost": aiops\_monthly\_cost,

`        `"roi": roi,

`        `"payback\_months": payback\_months,

`        `"verdict": "worth\_it" if roi **>** 1.5 else "marginal" if roi **>** 1.0 else "not\_worth\_it",

`    `}

**8.4 Break-even examples**

|**Scenario**|**Verdict**|**Reason**|
| :- | :- | :- |
|20 services, 2 incident/mo × 1h, $10k/h, $15k AIOps|ROI 0.53 → **not\_worth\_it**|Too few incidents to justify|
|100 services, 5 incident/mo × 2h, $20k/h, $25k AIOps|ROI 3.2 → **worth\_it**|Right size, real downtime cost|
|500 services, 10 incident/mo × 1.5h, $50k/h, $60k AIOps|ROI 5.0 → **worth\_it**|Scale + cost both high|
|10 services, 1 incident/mo × 30min, $1k/h, $10k AIOps|ROI 0.02 → **not\_worth\_it**|Hire good SRE instead|

**8.5 When NOT to do AIOps**

- < 30 services and < 3 incident/month
- Downtime cost < $1k/hour (internal tools, hobby)
- Observability stack chưa mature (no SLO, no centralized log) → AIOps không có signal sạch để work với
- Postmortem culture chưa establish → AIOps surface signal nhưng không ai action

Recipe đúng trong những case này: invest vào good observability + SLO + on-call culture, KHÔNG đầu tư AIOps.

-----
**9. Bài tập**

**9.1 Setup được cung cấp**

Download pack:

wget https://learning-notes-dz2.pages.dev/aiops-w3/lab/w3-d3-pack.zip

unzip w3-d3-pack.zip -d w3-d3-pack/

cd w3-d3-pack/

Cấu trúc pack:

outage\_catalog.yaml           # 5 outage có sẵn template reproduction

reproduction\_templates/

├── aws\_s3\_2017/              # operator typo + over-broad command

├── github\_mysql\_2018/        # MySQL primary-replica + orchestrator

├── cloudflare\_regex\_2019/    # FastAPI app với evil regex middleware  

├── roblox\_consul\_2021/       # Consul + nội dependency loop demo

└── slack\_2022/               # provisioning queue overload demo

pipeline/                     # AIOps pipeline (W1+W2 wired, sẵn endpoint)

templates/

├── postmortem\_template.md    # field-by-field template

├── adr\_template.md           # Nygard format

└── spec\_template.md          # SPEC.md outline

scripts/

├── start\_reproduction.sh     # spin up chosen reproduction stack

├── inject.sh                 # trigger failure mode

└── capture\_timeline.py       # record event timeline với UTC timestamp

**9.2 Bước 1 — Pick outage**

Chọn 1 outage từ outage\_catalog.yaml (5 lựa chọn ở §5).

Trong SUBMIT.md Section 0, viết:

**## Outage chosen**

\- ID: <1-5>

\- Name: <ví dụ AWS S3 2017-02-28>

\- Why this one: <2-3 câu — bạn quan tâm pattern gì>

\- Failure mode: <pick từ §4: cascading | split-brain | regex | capacity | monitoring-loop | operator>

**9.3 Bước 2 — Reproduce**

cd reproduction\_templates/<chosen\_outage>/

bash ../scripts/start\_reproduction.sh

*# wait healthcheck*

bash ../scripts/inject.sh

*# capture*

python ../scripts/capture\_timeline.py --duration 600 --out timeline.json

timeline.json chứa event được capture từ Prometheus + container event + pipeline output, có UTC timestamp.

**9.4 Bước 3 — Run AIOps pipeline trên reproduction**

Pipeline đã chạy nền (port 8000). Query:

curl http://localhost:8000/alerts?since**=**<inject\_start\_ts> > alerts\_observed.json

curl -X POST http://localhost:8000/rca \

`     `-d '{"window\_start": <ts>, "window\_end": <ts+600>}' \

`     `> rca\_observed.json

So sánh với expected (theo original postmortem):

- Pipeline detected sự cố trong < N giây? (target < 30s)
- Pipeline pick đúng root service không?
- Có pattern nào pipeline miss hoàn toàn không?

Note ít nhất **2 gap cụ thể** vào postmortem.md Section “Detection”.

**9.5 Bước 4 — Viết postmortem.md**

Theo template §2. Mỗi field bắt buộc fill, không bỏ trống. Timeline phải có **ít nhất 8 event** với UTC timestamp (lấy từ timeline.json).

Required wording check: 0 instance của “ did X” — chỉ chấp nhận blameless wording (§2.1).

**9.6 Bước 5 — Viết ADR.md**

1 ADR cho 1 design decision của AIOps platform, theo template Nygard §7.1. Decision phải:

- Có ít nhất 2 alternatives với pros/cons mỗi cái
- Có ít nhất 2 consequences (1 positive, 1 trade-off)
- Reference được gap đã quan sát ở §9.4

Ví dụ topic ADR phù hợp:

- RCA: count-based vs topology-aware vs causal-lag — pick gì
- Alert routing: page-everyone vs tier-based on-call rotation
- Detector: single threshold vs ensemble (3σ + IF + LSTM-AE)
- Storage: hot Prometheus 2 tuần vs S3+Athena cold long-term
- LLM: GPT-style cloud API vs self-host Llama vs no LLM

**9.7 Bước 6 — Viết cost\_model.py**

Implement function chính xác theo signature:

def is\_worth\_it(

`    `num\_services: int,

`    `incidents\_per\_month: int,

`    `avg\_incident\_duration\_hours: float,

`    `downtime\_cost\_per\_hour: float,

`    `expected\_mttr\_reduction\_pct: float **=** 0.4,

`    `aiops\_monthly\_cost: float **=** 15\_000,

) **->** dict:

`    `"""

`    `Returns:

`      `{

`        `"monthly\_value": float,

`        `"monthly\_cost": float,

`        `"roi": float,

`        `"payback\_months": float,  # or float('inf')

`        `"verdict": "worth\_it" | "marginal" | "not\_worth\_it"

`      `}

`    `Verdict rule:

`      `roi > 1.5 → worth\_it

`      `1.0 < roi ≤ 1.5 → marginal

`      `roi ≤ 1.0 → not\_worth\_it

`    `"""

Plus 3 worked example scenario in cùng file (call function + print result):

if \_\_name\_\_ **==** "\_\_main\_\_":

`    `print(is\_worth\_it(num\_services**=**20, incidents\_per\_month**=**2,

`                      `avg\_incident\_duration\_hours**=**1, downtime\_cost\_per\_hour**=**10\_000,

`                      `aiops\_monthly\_cost**=**15\_000))

`    `print(is\_worth\_it(num\_services**=**100, incidents\_per\_month**=**5,

`                      `avg\_incident\_duration\_hours**=**2, downtime\_cost\_per\_hour**=**20\_000,

`                      `aiops\_monthly\_cost**=**25\_000))

`    `*# 1 scenario của bạn — chọn industry, defend choice của downtime cost trong comment*

**9.8 Bước 7 — Viết SPEC.md (gộp W3 lại)**

Outline:

**# AIOps Mini-Platform Spec — <your name>**

**## 1. Platform overview**

[2-3 câu: stack được monitor, scope, user của platform]

**## 2. SLO definition (from W3-D1)**

[paste/reference slo\_spec.yaml — 3 service × SLI+SLO+budget]

**## 3. Detection + Correlation + RCA stack (from W1+W2)**

[1 paragraph mỗi layer — high-level approach + ADR reference]

**## 4. Reliability validation (from W3-D2)**

[paste chaos\_report.md scoreboard + top 3 gap]

**## 5. Operational pattern (from W3-D3)**

[reproduced outage + key learning + ADR-001 reference]

**## 6. Cost model (from W3-D3)**

[paste cost\_model.py output cho stack hiện tại + break-even point]

**## 7. Open risks**

[3-5 known gap chưa fix, mỗi cái có severity + mitigation plan]

**9.9 Bước 8 — SUBMIT.md**

**# W3-D3 Submission — <your name>**

**## Outage chosen**

[section §9.2]

**## 3 thứ tôi học từ outage này**

1\. ...

2\. ...

3\. ...

**## 1 thứ pipeline của tôi sẽ vẫn miss nếu outage này xảy ra real**

\- Pattern: ...

\- Why miss: ...

\- Mitigation idea: ...

**## 1 quyết định trong ADR mà tôi không hoàn toàn chắc**

...

**## Cost model verdict cho stack của tôi**

\- ROI: *\_\_*

\- Payback: *\_\_* tháng

\- Verdict: *\_\_*

**9.10 Acceptance checklist**

- ` `Reproduction chạy được, inject.sh trigger failure mode quan sát được
- ` `timeline.json có ≥ 8 event với UTC timestamp
- ` `postmortem.md có đủ field theo template §2, 0 blame wording, timeline ≥ 8 event, ≥ 2 gap detection được note
- ` `ADR.md có ≥ 2 alternatives mỗi cái có pros/cons, ≥ 2 consequences, reference 1 gap từ §9.4
- ` `cost\_model.py parse được, is\_worth\_it() return đúng schema, có 3 worked example
- ` `SPEC.md có 7 section đầy đủ
- ` `SUBMIT.md đủ 5 section
-----
**10. Deliverable summary**

|**File**|**Mô tả**|**Spec**|
| :- | :- | :- |
|reproduction/|Outage reproduction stack (docker-compose + inject + timeline)|§9.3|
|timeline.json|Captured event với UTC timestamp|§9.3|
|alerts\_observed.json + rca\_observed.json|Pipeline output trên reproduction|§9.4|
|postmortem.md|Blameless postmortem theo Google SRE template|§9.5, §2|
|ADR.md|1 architecture decision record cho AIOps platform|§9.6, §7.1|
|cost\_model.py|is\_worth\_it() + 3 scenario|§9.7|
|SPEC.md|Master spec gộp W3 deliverable|§9.8|
|SUBMIT.md|Reflection 5-section|§9.9|

Đường dẫn nộp: aiops-<tên>/w3/d3/.

-----
**11. Anti-patterns**

|**Anti-pattern**|**Hậu quả**|
| :- | :- |
|Postmortem blame cá nhân|Blame culture → bug ẩn lâu hơn → outage tệ hơn|
|Copy postmortem gốc thay vì viết từ reproduction|No learning, document = fiction|
|ADR thiếu Alternatives|Không phải decision record, chỉ là announcement|
|Cost model bỏ qua engineer time|Underestimate cost 3-5×|
|Reproduce outage 1:1 với prod|Đốt tiền + scope creep. Minimal env đủ để trigger pattern|
|Action item không có owner + due|Postmortem trở thành file lưu trữ, không ai làm|
|5 Whys khi outage có > 1 cause|Miss contributing factors → incomplete fix|

-----
**12. References**

|**Source**|**Topic**|**URL**|
| :- | :- | :- |
|Beyer et al. SRE Book Ch 15|Postmortem Culture (canonical Google framework)|<https://sre.google/sre-book/postmortem-culture/>|
|Michael Nygard (2011)|ADR format|<https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions>|
|Joel Parker Henderson|ADR templates collection|<https://github.com/joelparkerhenderson/architecture-decision-record>|
|danluu/post-mortems|Curated archive of public postmortems|<https://github.com/danluu/post-mortems>|
|GitHub Engineering|Oct 2018 MySQL split-brain analysis|<https://github.blog/2018-10-30-oct21-post-incident-analysis>|
|Cloudflare|July 2019 regex outage detail|<https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019>|
|Roblox|Oct 2021 73-hour Consul outage|<https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021>|
|AWS|Feb 2017 S3 us-east-1 disruption|<https://aws.amazon.com/message/41926/>|
|Slack Engineering|Jan 2022 incident retrospective|<https://slack.engineering/slacks-incident-on-2-22-22/>|
|Allspaw & Robbins|*Web Operations*, O’Reilly 2010 (causal tree pattern)|<https://www.oreilly.com/library/view/web-operations/9781449377465/>|
|Etsy|Blameless postmortem culture|<https://www.etsy.com/codeascraft/blameless-postmortems>|
|ITIC 2024|Hourly Cost of Downtime Survey|<https://itic-corp.com/>|
|Atlassian|Incident response handbook|<https://www.atlassian.com/incident-management>|

