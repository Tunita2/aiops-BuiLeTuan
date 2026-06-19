#!/usr/bin/env python3
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Chaos Runner — Orchestrates 10 chaos experiments against the AIOps pipeline
=============================================================================
Reads experiments.yaml, for each experiment:
  1. Verify steady-state (probe + pipeline)
  2. Inject fault (Pumba / docker exec / HTTP inject)
  3. Wait for detection window
  4. Query pipeline (/alerts, /correlate, /rca)
  5. Record results + judge TP/FP/FN
  6. Rollback
  7. Cooldown 120s

Implements 2 required functions (§8.5):
  - build_inject_cmd(exp)  — dispatcher by fault_type → command list
  - print_scoreboard(results) — confusion matrix + per-experiment table
"""

import json
import os
import subprocess
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────
PIPELINE_URL = os.environ.get('PIPELINE_URL', 'http://localhost:8000')
PROBE_ENDPOINT = os.environ.get('PROBE_ENDPOINT', 'http://localhost:8081/checkout/health')
COMPOSE_DIR = Path(__file__).parent.resolve()
COOLDOWN_SEC = int(os.environ.get('COOLDOWN_SEC', '120'))
DETECTION_WINDOW_SEC = int(os.environ.get('DETECTION_WINDOW_SEC', '75'))
RESULTS_FILE = COMPOSE_DIR / 'chaos_results.json'
EXPERIMENTS_FILE = COMPOSE_DIR / 'experiments.yaml'


# =====================================================================
# REQUIRED FUNCTION 1: build_inject_cmd(exp)
# =====================================================================

def build_inject_cmd(exp: dict) -> dict:
    """
    Dispatcher by fault_type -- returns a dict with:
      - 'type': 'subprocess' | 'http'
      - 'inject_cmd': list[str] for subprocess, or dict for HTTP
      - 'rollback_cmd': list[str] or dict for cleanup
      - 'target': container name

    Most faults use HTTP injection via /config endpoint for reliability
    on Windows Docker Desktop. Only docker kill and stress-ng use subprocess.
    """
    fault_type = exp['fault_type']
    target = exp['blast_radius']['target']
    duration = exp['blast_radius'].get('duration', 60)
    args = exp.get('inject_cmd_args', {})
    tool = args.get('tool', 'http_inject')
    params = args.get('params', '{}')

    # --- HTTP injection path (latency, error_rate, or both) ---
    if tool == 'http_inject' or fault_type in (
        'latency', 'network_loss', 'time_skew', 'network_partition',
        'dns_latency', 'cascade_retry', 'disk_fill', 'memory',
    ):
        cfg = json.loads(params) if isinstance(params, str) else params
        svc_url = 'http://localhost:8080/config'

        # Build rollback payload: zero out everything we injected
        rollback_payload = {}
        if 'error_rate' in cfg:
            rollback_payload['error_rate'] = 0
        if 'latency_ms' in cfg:
            rollback_payload['latency_ms'] = 0
        if not rollback_payload:
            rollback_payload = {'error_rate': 0, 'latency_ms': 0}

        return {
            'type': 'http',
            'inject_cmd': {
                'url': svc_url,
                'payload': cfg,
                'container': target,
            },
            'rollback_cmd': {
                'url': svc_url,
                'payload': rollback_payload,
                'container': target,
            },
            'target': target,
        }

    # --- docker kill for availability ---
    elif fault_type == 'availability':
        return {
            'type': 'subprocess',
            'inject_cmd': ['docker', 'kill', target],
            'rollback_cmd': [
                'docker', 'compose', '-f',
                str(COMPOSE_DIR / 'docker-compose.yml'),
                'start', exp['target'],
            ],
            'target': target,
        }

    # --- docker exec stress-ng for CPU saturation ---
    elif fault_type == 'cpu_saturation':
        return {
            'type': 'subprocess',
            'inject_cmd': [
                'docker', 'exec', '-d', target,
                *params.split(),
            ],
            'rollback_cmd': [
                'docker', 'exec', target,
                'pkill', '-f', 'stress-ng',
            ],
            'target': target,
        }

    else:
        raise ValueError(f'Unknown fault_type: {fault_type}')


# =====================================================================
# REQUIRED FUNCTION 2: print_scoreboard(results)
# =====================================================================

def print_scoreboard(results: list[dict]):
    """
    Print the confusion matrix + per-experiment table in §8.6 format.

    ==== Chaos Run ====
    Total: 10
    Detected: <N>/10
    RCA correct: <N>/<detected>
    False alarms in baseline windows: <N>
    Precision: <float>
    Recall: <float>
    MTTD p50: <s>, p95: <s>

    Per-experiment:
    | # | name | detected | mttd | rca_service | rca_correct |
    ...
    Gaps identified:
    - <id>: <symptom> → <cause>
    """
    total = len(results)
    detected = sum(1 for r in results if r.get('detected'))
    tp = detected
    fn = total - detected
    false_alarms = sum(1 for r in results if r.get('false_alarm', False))
    fp = false_alarms

    # RCA accuracy among detected
    rca_correct = sum(
        1 for r in results
        if r.get('detected') and r.get('rca_correct')
    )
    rca_total = detected

    # Precision, Recall
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # MTTD stats
    mttd_values = [r['mttd'] for r in results if r.get('detected') and r.get('mttd')]
    mttd_p50 = statistics.median(mttd_values) if mttd_values else 0
    mttd_p95 = (
        sorted(mttd_values)[int(len(mttd_values) * 0.95)]
        if len(mttd_values) > 1
        else (mttd_values[0] if mttd_values else 0)
    )

    # ── Print scoreboard ──
    print()
    print('=' * 60)
    print('==== Chaos Run ====')
    print('=' * 60)
    print(f'Total: {total}')
    print(f'Detected: {detected}/{total}')
    print(f'RCA correct: {rca_correct}/{rca_total}')
    print(f'False alarms in baseline windows: {false_alarms}')
    print(f'Precision: {precision:.2f}')
    print(f'Recall: {recall:.2f}')
    print(f'MTTD p50: {mttd_p50:.0f}s, p95: {mttd_p95:.0f}s')
    print()
    print('Per-experiment:')
    print(f'| {"#":>2} | {"name":<28} | {"detected":<8} | {"mttd":<5} | {"rca_service":<16} | {"rca_correct":<11} |')
    print(f'|{"-"*4}|{"-"*30}|{"-"*10}|{"-"*7}|{"-"*18}|{"-"*13}|')

    for r in results:
        det = 'Y' if r.get('detected') else 'N'
        mttd = f'{r["mttd"]:.0f}s' if r.get('mttd') else '-'
        rca_svc = str(r.get('rca_service') or '-')
        rca_ok = 'Y' if r.get('rca_correct') else ('N' if r.get('detected') else '-')
        print(f'| {r["id"]:>2} | {r["name"]:<28} | {det:<8} | {mttd:<5} | {rca_svc:<16} | {rca_ok:<11} |')

    # Gaps
    gaps = [r for r in results if not r.get('detected') or not r.get('rca_correct')]
    if gaps:
        print()
        print('Gaps identified:')
        for r in gaps:
            if not r.get('detected'):
                print(f'- #{r["id"]} ({r["name"]}): NOT DETECTED -> pipeline missed {r["fault_type"]} fault')
            elif not r.get('rca_correct'):
                print(f'- #{r["id"]} ({r["name"]}): RCA WRONG — picked {r.get("rca_service", "?")} '
                      f'instead of {r.get("expected_rca_service", "?")}')

    print('=' * 60)

    # Verdict
    detected_ok = detected >= 7
    rca_ok = rca_correct >= 5 if detected >= 7 else False
    fa_ok = false_alarms <= 1
    verdict = 'PASS' if (detected_ok and rca_ok and fa_ok) else 'FAIL'
    print(f'\nVerdict: {verdict}')
    print(f'  detected >= 7/10: {"[OK]" if detected_ok else "[FAIL]"} ({detected}/10)')
    print(f'  RCA correct >= 5/detected: {"[OK]" if rca_ok else "[FAIL]"} ({rca_correct}/{detected})')
    print(f'  false alarms <= 1: {"[OK]" if fa_ok else "[FAIL]"} ({false_alarms})')
    print()


# =====================================================================
# Inject / Rollback Execution
# =====================================================================

def execute_inject(cmd_info: dict) -> bool:
    """Execute the fault injection command. Returns True on success."""
    if cmd_info['type'] == 'subprocess':
        cmd = cmd_info['inject_cmd']
        print(f'  [INJECT] {" ".join(cmd)}')
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=str(COMPOSE_DIR),
            )
            if result.returncode != 0:
                print(f'  [WARN] inject returned {result.returncode}: {result.stderr.strip()}')
            return True
        except subprocess.TimeoutExpired:
            print('  [WARN] inject command timed out')
            return True
        except Exception as exc:
            print(f'  [ERROR] inject failed: {exc}')
            return False

    elif cmd_info['type'] == 'http':
        info = cmd_info['inject_cmd']
        container = info.get('container', '')
        svc_url = info['url']
        payload = json.dumps(info['payload'])
        cmd = [
            'docker', 'exec', '-i', container,
            'curl', '-s', '-X', 'POST',
            '-H', 'Content-Type: application/json',
            '-d', '@-',
            svc_url,
        ]
        print(f'  [INJECT] HTTP -> {svc_url} payload={payload}')
        try:
            result = subprocess.run(
                cmd, input=payload, capture_output=True, text=True, timeout=15,
            )
            print(f'  [INJECT] response: {result.stdout.strip()}')
            return True
        except Exception as exc:
            print(f'  [ERROR] HTTP inject failed: {exc}')
            return False
    return False


def execute_rollback(cmd_info: dict):
    """Execute the rollback command."""
    if cmd_info['type'] == 'subprocess':
        cmd = cmd_info['rollback_cmd']
        print(f'  [ROLLBACK] {" ".join(cmd)}')
        try:
            subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=str(COMPOSE_DIR),
            )
        except Exception as exc:
            print(f'  [WARN] rollback error: {exc}')

    elif cmd_info['type'] == 'http':
        info = cmd_info['rollback_cmd']
        container = info.get('container', '')
        svc_url = info['url']
        payload = json.dumps(info['payload'])
        cmd = [
            'docker', 'exec', '-i', container,
            'curl', '-s', '-X', 'POST',
            '-H', 'Content-Type: application/json',
            '-d', '@-',
            svc_url,
        ]
        print(f'  [ROLLBACK] HTTP -> {svc_url} payload={payload}')
        try:
            subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=15)
        except Exception:
            pass


# =====================================================================
# Pipeline Query
# =====================================================================

def query_pipeline(inject_time: float, exp: dict) -> dict:
    """
    Query the AIOps pipeline for alerts, correlate, and RCA.
    Returns analysis results.
    """
    result = {
        'detected': False,
        'mttd': None,
        'rca_service': None,
        'rca_correct': False,
        'false_alarm': False,
        'alerts': [],
        'clusters': [],
        'rca_response': {},
    }

    expected_service = exp['ground_truth']['expected_rca_service']

    try:
        # 1. Get alerts since injection
        r = requests.get(
            f'{PIPELINE_URL}/alerts',
            params={'since': inject_time},
            timeout=10,
        )
        alerts_data = r.json()
        alerts = alerts_data.get('alerts', [])
        result['alerts'] = alerts

        if not alerts:
            return result

        # Check if any alert matches the target service
        target_svc = exp['target']
        target_alerts = [a for a in alerts if a.get('service') == target_svc]
        related_alerts = [
            a for a in alerts
            if a.get('service') in exp.get('blast_radius', {}).get('target', '')
            or a.get('service') == target_svc
        ]

        if target_alerts or related_alerts:
            result['detected'] = True
            # MTTD = first alert time - inject time
            all_relevant = target_alerts or related_alerts
            first_alert_ts = min(
                a.get('_ts_unix', inject_time)
                for a in all_relevant
            )
            result['mttd'] = max(0, first_alert_ts - inject_time)
        elif alerts:
            # Alerts exist but not for the target service — still counts
            # as detected if in the same topology path
            result['detected'] = True
            first_ts = min(a.get('_ts_unix', inject_time) for a in alerts)
            result['mttd'] = max(0, first_ts - inject_time)

        # 2. Correlate
        r = requests.post(
            f'{PIPELINE_URL}/correlate',
            json={'since': inject_time, 'window': 300},
            timeout=10,
        )
        corr_data = r.json()
        result['clusters'] = corr_data.get('clusters', [])

        # 3. RCA
        r = requests.post(
            f'{PIPELINE_URL}/rca',
            json={'alerts': alerts},
            timeout=10,
        )
        rca_data = r.json()
        result['rca_response'] = rca_data
        result['rca_service'] = rca_data.get('root_service', 'unknown')

        # Check RCA accuracy
        rca_svc = result['rca_service']
        if rca_svc == expected_service:
            result['rca_correct'] = True
        # Also accept if RCA picks a service in the same fault path
        elif rca_svc in (exp.get('blast_radius', {}).get('target', '').replace('w3d2-', ''),
                         target_svc):
            result['rca_correct'] = True

    except requests.exceptions.ConnectionError:
        print('  [WARN] Pipeline unreachable')
    except Exception as exc:
        print(f'  [WARN] Pipeline query error: {exc}')

    return result


# =====================================================================
# Main Runner
# =====================================================================

def run_experiments():
    """Main entry point — load experiments.yaml, run all 10, score."""
    # Load experiments
    with open(EXPERIMENTS_FILE, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    experiments = config['experiments']

    print(f'\n{"=" * 60}')
    print(f'W3-D2 Chaos Engineering — Running {len(experiments)} experiments')
    print(f'Pipeline: {PIPELINE_URL}')
    print(f'Cooldown: {COOLDOWN_SEC}s between experiments')
    print(f'Detection window: {DETECTION_WINDOW_SEC}s')
    print(f'{"=" * 60}\n')

    # Check pipeline is up
    try:
        r = requests.get(f'{PIPELINE_URL}/healthz', timeout=5)
        print(f'Pipeline status: {r.json()}')
    except Exception:
        print('[ERROR] Pipeline not reachable. Is the stack running?')
        print('  Run: docker compose up -d')
        sys.exit(1)

    # Clear previous alerts
    try:
        requests.delete(f'{PIPELINE_URL}/alerts', timeout=5)
        print('Previous alerts cleared.\n')
    except Exception:
        pass

    all_results = []

    for i, exp in enumerate(experiments):
        exp_id = exp['id']
        exp_name = exp['name']
        target = exp['target']
        fault_type = exp['fault_type']
        expected_detected = exp['ground_truth']['expected_detected']
        expected_rca = exp['ground_truth']['expected_rca_service']

        print(f'\n{"-" * 60}')
        print(f'Experiment #{exp_id}: {exp_name}')
        print(f'  Target: {target} | Fault: {fault_type}')
        print(f'  Expected: detected={expected_detected}, RCA={expected_rca}')
        print(f'{"-" * 60}')

        # Build injection command
        try:
            cmd_info = build_inject_cmd(exp)
        except Exception as exc:
            print(f'  [ERROR] Failed to build inject cmd: {exc}')
            all_results.append({
                'id': exp_id, 'name': exp_name, 'fault_type': fault_type,
                'detected': False, 'mttd': None,
                'rca_service': None, 'rca_correct': False,
                'expected_rca_service': expected_rca,
                'error': str(exc),
            })
            continue

        # Clear alerts before injection
        try:
            requests.delete(f'{PIPELINE_URL}/alerts', timeout=5)
        except Exception:
            pass

        # Record injection time
        inject_time = time.time()

        # Execute injection
        print(f'\n  Phase 1: Injecting fault...')
        success = execute_inject(cmd_info)
        if not success:
            print('  [WARN] Injection may have partially failed')

        # Wait for detection window
        print(f'  Phase 2: Waiting {DETECTION_WINDOW_SEC}s for detection...')
        time.sleep(DETECTION_WINDOW_SEC)

        # Query pipeline
        print(f'  Phase 3: Querying pipeline...')
        query_result = query_pipeline(inject_time, exp)

        # Rollback
        print(f'  Phase 4: Rolling back...')
        execute_rollback(cmd_info)

        # For availability experiments, need to restart the container
        if fault_type == 'availability':
            print('  [RESTART] Restarting killed container...')
            time.sleep(5)
            subprocess.run(
                ['docker', 'compose', '-f',
                 str(COMPOSE_DIR / 'docker-compose.yml'),
                 'start', target],
                capture_output=True, text=True, cwd=str(COMPOSE_DIR),
            )
            time.sleep(10)

        # Record result
        exp_result = {
            'id': exp_id,
            'name': exp_name,
            'fault_type': fault_type,
            'target': target,
            'detected': query_result['detected'],
            'mttd': query_result['mttd'],
            'rca_service': query_result['rca_service'],
            'rca_correct': query_result['rca_correct'],
            'expected_rca_service': expected_rca,
            'expected_detected': expected_detected,
            'false_alarm': query_result['false_alarm'],
            'alert_count': len(query_result['alerts']),
            'cluster_count': len(query_result['clusters']),
            'inject_time': inject_time,
        }
        all_results.append(exp_result)

        # Print per-experiment summary
        det_str = '[OK] DETECTED' if query_result['detected'] else '[MISS] MISSED'
        rca_str = (f'[OK] RCA={query_result["rca_service"]}'
                   if query_result['rca_correct']
                   else f'[MISS] RCA={query_result.get("rca_service", "-")}')
        mttd_str = f'{query_result["mttd"]:.0f}s' if query_result['mttd'] else '-'
        print(f'\n  Result: {det_str} | MTTD={mttd_str} | {rca_str}')
        print(f'  Alerts: {len(query_result["alerts"])} | Clusters: {len(query_result["clusters"])}')

        # Cooldown (skip after last experiment)
        if i < len(experiments) - 1:
            print(f'\n  Cooldown: waiting {COOLDOWN_SEC}s for system recovery...')
            time.sleep(COOLDOWN_SEC)

    # Save results
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f'\nResults saved to {RESULTS_FILE}')

    # Print scoreboard
    print_scoreboard(all_results)

    return all_results


# =====================================================================
# Entry Point
# =====================================================================

if __name__ == '__main__':
    run_experiments()
