"""Quick analysis script for DESIGN.md data support."""
import json
from pathlib import Path
from collections import Counter, defaultdict

data = Path("data")

# 1. Frontend signal analysis
print("=== FRONTEND SIGNAL ANALYSIS ===")
total = 0
js_err = 0
net_err = 0
dom_slow = 0  # > 3000ms
dom_values = []
for line in (data / "frontend_rum.jsonl").open():
    ev = json.loads(line)
    total += 1
    if ev["js_error"]: js_err += 1
    if ev["network_error"]: net_err += 1
    if ev["dom_ready_ms"] >= 3000: dom_slow += 1
    dom_values.append(ev["dom_ready_ms"])

print(f"Total: {total}")
print(f"JS error rate: {js_err/total:.4%} ({js_err})")
print(f"Network error rate: {net_err/total:.4%} ({net_err})")
print(f"DOM > 3000ms rate: {dom_slow/total:.4%} ({dom_slow})")
dom_values.sort()
for p in [50, 90, 95, 99, 99.5, 99.9]:
    idx = int(len(dom_values) * p / 100)
    print(f"DOM p{p}: {dom_values[idx]}ms")

# 2. API latency distribution
print("\n=== API LATENCY DISTRIBUTION ===")
latencies = []
status_counts = Counter()
path_4xx = defaultdict(lambda: {"total": 0, "4xx": 0})

for line in (data / "access_log.jsonl").open():
    ev = json.loads(line)
    latencies.append(ev["latency_ms"])
    s = ev["status"]
    if 400 <= s < 500 and s != 429:
        status_counts["4xx_not_429"] += 1
        path_4xx[ev["path"]]["4xx"] += 1
    elif s == 429:
        status_counts["429"] += 1
    elif s >= 500:
        status_counts["5xx"] += 1
    else:
        status_counts["2xx_3xx"] += 1
    path_4xx[ev["path"]]["total"] += 1

latencies.sort()
total_api = len(latencies)
print(f"Total API requests: {total_api}")
for p in [50, 90, 95, 99, 99.5, 99.9]:
    idx = int(total_api * p / 100)
    print(f"Latency p{p}: {latencies[idx]}ms")

print(f"\nStatus distribution:")
for k, v in sorted(status_counts.items()):
    print(f"  {k}: {v} ({v/total_api:.4%})")

# 3. Per-path 4xx rates
print("\n=== 4xx RATE PER PATH ===")
for path, counts in sorted(path_4xx.items()):
    rate = counts["4xx"] / counts["total"] if counts["total"] else 0
    print(f"  {path}: {rate:.4%} ({counts['4xx']}/{counts['total']})")

# 4. DB analysis
print("\n=== DB ANALYSIS ===")
durations = []
db_fail = 0
db_total = 0
for line in (data / "db_query_log.jsonl").open():
    ev = json.loads(line)
    db_total += 1
    durations.append(ev["duration_ms"])
    if not ev["success"]:
        db_fail += 1

durations.sort()
print(f"Total queries: {db_total}, Failures: {db_fail} ({db_fail/db_total:.4%})")
for p in [50, 90, 95, 99, 99.5, 99.9]:
    idx = int(db_total * p / 100)
    print(f"Duration p{p}: {durations[idx]}ms")
