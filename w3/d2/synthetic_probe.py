#!/usr/bin/env python3
"""
Synthetic Probe — External steady-state signal (§6.4)
======================================================
Runs outside the Docker network, pings /checkout/health every 5s.
Logs pass/fail to probe.log.
Steady-state = >= 99% pass rate in a 60s window.
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone

ENDPOINT = os.environ.get('PROBE_ENDPOINT', 'http://localhost:8081/checkout/health')
LOG_FILE = os.environ.get('PROBE_LOG', 'probe.log')
INTERVAL = int(os.environ.get('PROBE_INTERVAL', '5'))
TIMEOUT = int(os.environ.get('PROBE_TIMEOUT', '2'))


def main():
    print(f'Synthetic Probe starting...')
    print(f'  Endpoint: {ENDPOINT}')
    print(f'  Log file: {LOG_FILE}')
    print(f'  Interval: {INTERVAL}s')

    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        while True:
            ts = int(time.time())
            start = time.time()
            try:
                r = requests.get(ENDPOINT, timeout=TIMEOUT)
                latency_ms = int((time.time() - start) * 1000)
                if r.status_code == 200 and latency_ms < 500:
                    line = f'{ts} pass {latency_ms}'
                else:
                    line = f'{ts} fail {r.status_code} {latency_ms}'
            except requests.exceptions.Timeout:
                latency_ms = int((time.time() - start) * 1000)
                line = f'{ts} fail timeout {latency_ms}'
            except requests.exceptions.ConnectionError:
                latency_ms = int((time.time() - start) * 1000)
                line = f'{ts} fail connection_error {latency_ms}'
            except Exception as e:
                latency_ms = int((time.time() - start) * 1000)
                line = f'{ts} fail {type(e).__name__} {latency_ms}'

            f.write(line + '\n')
            f.flush()
            print(line)
            time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
