#!/usr/bin/env python3
"""
query_pipeline.py — Test the AIOps pipeline endpoints
======================================================
Call /alerts + /correlate + /rca and pretty-print results.
"""

import json
import sys
import time
import requests

PIPELINE_URL = 'http://localhost:8000'


def main():
    print('=' * 50)
    print('AIOps Pipeline Query Tool')
    print('=' * 50)

    # 1. Health check
    print('\n[1] GET /healthz')
    try:
        r = requests.get(f'{PIPELINE_URL}/healthz', timeout=5)
        print(f'  Status: {r.status_code}')
        print(f'  Response: {r.json()}')
    except Exception as e:
        print(f'  ERROR: {e}')
        sys.exit(1)

    # 2. Version
    print('\n[2] GET /version')
    try:
        r = requests.get(f'{PIPELINE_URL}/version', timeout=5)
        print(f'  {json.dumps(r.json(), indent=2)}')
    except Exception as e:
        print(f'  ERROR: {e}')

    # 3. Alerts
    since = time.time() - 600  # last 10 minutes
    print(f'\n[3] GET /alerts?since={since:.0f}')
    try:
        r = requests.get(f'{PIPELINE_URL}/alerts', params={'since': since}, timeout=10)
        data = r.json()
        print(f'  Alert count: {data.get("count", 0)}')
        for a in data.get('alerts', [])[:5]:
            print(f'    [{a["severity"]}] {a["service"]}/{a["metric"]} = {a["value"]}')
        if data.get('count', 0) > 5:
            print(f'    ... and {data["count"] - 5} more')
    except Exception as e:
        print(f'  ERROR: {e}')

    # 4. Correlate
    print(f'\n[4] POST /correlate')
    try:
        r = requests.post(
            f'{PIPELINE_URL}/correlate',
            json={'window': 600},
            timeout=10,
        )
        data = r.json()
        print(f'  Clusters: {data.get("cluster_count", 0)}')
        for c in data.get('clusters', []):
            print(f'    [{c["cluster_id"]}] {c["alert_count"]} alerts, services={c["services"]}')
    except Exception as e:
        print(f'  ERROR: {e}')

    # 5. RCA
    print(f'\n[5] POST /rca')
    try:
        r = requests.post(
            f'{PIPELINE_URL}/rca',
            json={},
            timeout=10,
        )
        data = r.json()
        print(f'  Root service: {data.get("root_service", "?")}')
        print(f'  Confidence: {data.get("confidence", 0)}')
        print(f'  Evidence: {data.get("evidence", "")[:200]}')
    except Exception as e:
        print(f'  ERROR: {e}')

    print('\n' + '=' * 50)


if __name__ == '__main__':
    main()
