#!/usr/bin/env python3
"""
capture_baseline.py — Snapshot Prometheus metrics for N minutes → baseline.json
================================================================================
Captures steady-state metrics (mean + p99) for all monitored services.
Used by the anomaly detector to calibrate thresholds.
"""

import argparse
import json
import time
import sys
import requests
from datetime import datetime, timezone

PROMETHEUS_URL = 'http://localhost:9090'
SERVICES = [
    'frontend', 'api-gateway', 'payment-svc', 'inventory-svc',
    'notification-svc', 'checkout-svc', 'auth-svc',
    'log-collector', 'dns-resolver', 'cache-svc',
]


def prom_query(query: str) -> list:
    try:
        r = requests.get(
            f'{PROMETHEUS_URL}/api/v1/query',
            params={'query': query},
            timeout=10,
        )
        data = r.json()
        if data.get('status') == 'success':
            return data['data']['result']
    except Exception as e:
        print(f'  PromQL error: {e}')
    return []


def capture(duration_sec: int = 300) -> dict:
    """Capture baseline metrics over duration_sec seconds."""
    print(f'Capturing baseline for {duration_sec}s...')
    print(f'Prometheus: {PROMETHEUS_URL}')

    # Wait for metrics to accumulate
    samples = []
    interval = 15  # sample every 15s
    iterations = max(1, duration_sec // interval)

    for i in range(iterations):
        ts = datetime.now(timezone.utc).isoformat()
        sample = {'ts': ts, 'services': {}}

        for svc in SERVICES:
            svc_data = {}

            # Latency p99
            result = prom_query(
                f'histogram_quantile(0.99, '
                f'sum(rate(http_request_duration_seconds_bucket{{service="{svc}"}}[1m])) by (le))'
            )
            if result:
                svc_data['latency_p99'] = float(result[0]['value'][1])

            # Error rate
            result = prom_query(
                f'sum(rate(http_errors_total{{service="{svc}"}}[1m]))'
            )
            if result:
                svc_data['error_rate'] = float(result[0]['value'][1])

            # Request rate
            result = prom_query(
                f'sum(rate(http_requests_total{{service="{svc}"}}[1m]))'
            )
            if result:
                svc_data['request_rate'] = float(result[0]['value'][1])

            sample['services'][svc] = svc_data

        samples.append(sample)
        progress = (i + 1) / iterations * 100
        print(f'  Sample {i+1}/{iterations} ({progress:.0f}%)')

        if i < iterations - 1:
            time.sleep(interval)

    # Compute mean baseline
    baseline = {}
    for svc in SERVICES:
        values = {
            'latency_p99': [],
            'error_rate': [],
            'request_rate': [],
        }
        for s in samples:
            svc_data = s['services'].get(svc, {})
            for key in values:
                if key in svc_data and svc_data[key] is not None:
                    try:
                        val = float(svc_data[key])
                        if not (val != val):  # skip NaN
                            values[key].append(val)
                    except (ValueError, TypeError):
                        pass

        baseline[svc] = {}
        for key, vals in values.items():
            if vals:
                baseline[svc][f'{key}_mean'] = round(sum(vals) / len(vals), 6)
                baseline[svc][f'{key}_max'] = round(max(vals), 6)
            else:
                baseline[svc][f'{key}_mean'] = 0.0
                baseline[svc][f'{key}_max'] = 0.0

    return {
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'duration_sec': duration_sec,
        'sample_count': len(samples),
        'services': baseline,
    }


def main():
    parser = argparse.ArgumentParser(description='Capture baseline metrics')
    parser.add_argument('--duration', type=int, default=300, help='Duration in seconds (default: 300)')
    parser.add_argument('--out', type=str, default='baseline.json', help='Output file')
    args = parser.parse_args()

    baseline = capture(args.duration)

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(baseline, f, indent=2)

    print(f'\nBaseline saved to {args.out}')
    print(f'Services captured: {len(baseline["services"])}')
    for svc, data in baseline['services'].items():
        print(f'  {svc}: p99={data.get("latency_p99_mean", 0):.4f}s, '
              f'err_rate={data.get("error_rate_mean", 0):.6f}')


if __name__ == '__main__':
    main()
