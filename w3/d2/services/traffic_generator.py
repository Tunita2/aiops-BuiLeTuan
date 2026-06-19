"""
Traffic Generator — continuous synthetic load through the service mesh.
Ensures Prometheus has meaningful metrics to scrape during chaos experiments.
"""

import time
import random
import logging
import sys

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [traffic-gen] %(levelname)s %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('traffic-gen')

# Endpoints to hit — covers the full topology
TARGETS = [
    'http://api-gateway:8080/api/checkout',
    'http://api-gateway:8080/api/browse',
    'http://api-gateway:8080/api/search',
    'http://api-gateway:8080/api/payment',
    'http://frontend:8080/api/home',
    'http://checkout-svc:8080/api/process',
    'http://payment-svc:8080/api/charge',
    'http://inventory-svc:8080/api/stock',
    'http://notification-svc:8080/api/send',
    'http://auth-svc:8080/api/verify',
    'http://cache-svc:8080/api/get',
    'http://dns-resolver:8080/api/resolve',
    'http://log-collector:8080/api/ingest',
]

STARTUP_DELAY = 20  # seconds — wait for services to boot


def main():
    logger.info(f'Waiting {STARTUP_DELAY}s for services to start...')
    time.sleep(STARTUP_DELAY)
    logger.info('Starting traffic generation')

    cycle = 0
    while True:
        cycle += 1
        for url in TARGETS:
            try:
                r = requests.get(url, timeout=5)
                if cycle % 50 == 0:
                    logger.info(f'{url} -> {r.status_code}')
            except requests.exceptions.Timeout:
                logger.warning(f'{url} -> TIMEOUT')
            except requests.exceptions.ConnectionError:
                if cycle % 20 == 0:
                    logger.warning(f'{url} -> CONNECTION_ERROR')
            except Exception as exc:
                logger.error(f'{url} -> {type(exc).__name__}: {exc}')

            # Stagger requests slightly
            time.sleep(random.uniform(0.1, 0.3))

        # Small pause between full cycles
        time.sleep(random.uniform(0.5, 1.5))


if __name__ == '__main__':
    main()
