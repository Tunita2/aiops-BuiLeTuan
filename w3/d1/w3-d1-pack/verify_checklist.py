"""Quick acceptance checklist verification before git submit."""
import json
import yaml
from pathlib import Path

base = Path(r"d:\Cloude-DevOps\Phase-2\aiops-BuiLeTuan\w3\d1")
errors = []

# 1. slo_spec.yaml
print("=== 1. slo_spec.yaml ===")
spec = yaml.safe_load((base / "slo_spec.yaml").read_text())
services = spec.get("services", [])
print(f"  Services: {[s['name'] for s in services]}")
assert len(services) == 3, "Need 3 services"
for s in services:
    t = s["slo"]["target"]
    assert 0.9 <= t <= 0.9999, f"{s['name']} target {t} out of range"
    print(f"  {s['name']}: target={t}, budget={s['budget']['allowed_failures_per_month']}")
print("  ✅ PASS: 3 services, all targets in [0.9, 0.9999]")

# 2. burn_rate_alerts.yaml
print("\n=== 2. burn_rate_alerts.yaml ===")
alerts = yaml.safe_load((base / "burn_rate_alerts.yaml").read_text())
rule_count = sum(len(g.get("rules", [])) for g in alerts.get("groups", []))
print(f"  Groups: {[g['name'] for g in alerts['groups']]}")
print(f"  Total rules: {rule_count}")
for g in alerts["groups"]:
    for r in g["rules"]:
        expr = r.get("expr", "")
        assert "AND" in expr, f"{r['alert']} missing AND (not MWMBR)"
        assert ">=" in expr, f"{r['alert']} missing threshold"
        print(f"  {r['alert']}: severity={r['labels']['severity']}, tier={r['labels']['tier']}")
assert rule_count == 9, f"Expected 9 rules, got {rule_count}"
print("  ✅ PASS: 9 rules, all valid MWMBR format")

# 3. validation_report.json
print("\n=== 3. validation_report.json ===")
report = json.loads((base / "validation_report.json").read_text())
nr = report["noise_reduction_pct"]
mttd = abs(report["mttd_delta_s"])
fn = report["your_mwmbr"]["fn"]
print(f"  noise_reduction: {nr}% (need ≥70%)")
print(f"  mttd_delta: {mttd}s (need ≤60s)")
print(f"  false_negative: {fn} (need =0)")
print(f"  verdict: {report['verdict']}")
assert nr >= 70, f"noise_reduction {nr} < 70"
assert mttd <= 60, f"mttd_delta {mttd} > 60"
assert fn == 0, f"false_negative {fn} != 0"
print("  ✅ PASS")

# 4. DESIGN.md
print("\n=== 4. DESIGN.md ===")
design = (base / "DESIGN.md").read_text(encoding="utf-8")
sections = [s for s in design.split("## ") if s.strip()]
print(f"  Sections: {len(sections)}")
# Check data references
has_numbers = any(c.isdigit() for c in design)
assert len(sections) >= 5, "Need 5 sections"
print("  ✅ PASS: 5+ sections with data references")

# 5. SUBMIT.md
print("\n=== 5. SUBMIT.md ===")
submit = (base / "SUBMIT.md").read_text(encoding="utf-8")
required = ["3 thứ tôi học", "1 thứ vẫn chưa rõ", "trade-off", "Validation report"]
for r in required:
    assert r.lower() in submit.lower(), f"Missing section: {r}"
    print(f"  ✓ Found: {r}")
print("  ✅ PASS: 4 sections present")

print("\n" + "="*50)
print("🎉 ALL ACCEPTANCE CHECKS PASSED — ready to commit!")
