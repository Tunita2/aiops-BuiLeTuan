#!/usr/bin/env python3
"""
score_run.py — Compute scoreboard from chaos_results.json
==========================================================
"""

import json
import sys
import os

# Add parent dir to path so we can import chaos_runner
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chaos_runner import print_scoreboard


def main():
    results_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'chaos_results.json',
    )
    if not os.path.exists(results_file):
        print(f'ERROR: {results_file} not found. Run chaos_runner.py first.')
        sys.exit(1)

    with open(results_file, 'r', encoding='utf-8') as f:
        results = json.load(f)

    print_scoreboard(results)


if __name__ == '__main__':
    main()
