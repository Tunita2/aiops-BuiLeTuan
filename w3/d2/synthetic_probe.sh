#!/usr/bin/env bash
# synthetic_probe.sh — log pass/fail every 5s, use as steady-state signal (§6.4)

ENDPOINT="${1:-http://localhost:8080/checkout/health}"
LOG="${2:-probe.log}"

echo "Synthetic Probe: $ENDPOINT → $LOG"

while true; do
  ts=$(date -u +%s)
  start=$(date +%s%N)
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$ENDPOINT")
  end=$(date +%s%N)
  latency_ms=$(( (end - start) / 1000000 ))

  if [[ "$code" == "200" && "$latency_ms" -lt 500 ]]; then
    echo "$ts pass $latency_ms" >> "$LOG"
  else
    echo "$ts fail $code $latency_ms" >> "$LOG"
  fi

  sleep 5
done
