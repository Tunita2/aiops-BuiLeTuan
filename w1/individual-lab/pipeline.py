#!/usr/bin/env python3
"""AIOps W1 Individual Lab — Streaming Anomaly Pipeline.

HTTP server that receives metrics + logs from stream_generator,
detects anomalies in real-time, and writes alerts to alerts.jsonl.

Usage:
    uv run python pipeline.py
"""

from fastapi import FastAPI, Request
import json
import uvicorn
from collections import deque
import statistics

app = FastAPI(title="AIOps Anomaly Pipeline")
ALERTS_FILE = "alerts.jsonl"

# ---------------------------------------------------------------------------
# Sliding-window anomaly detector
# ---------------------------------------------------------------------------

WINDOW_SIZE = 20  # giữ 20 data points gần nhất để tính baseline


class AnomalyDetector:
    """Phát hiện 3 loại fault bằng multi-signal correlation + static thresholds.

    Thresholds được chọn dựa trên bảng "Khoảng bình thường" trong đề bài,
    đặt cao hơn giới hạn max của normal range để tránh false positive.
    """

    # --- Ngưỡng phát hiện (ngoài vùng bình thường) -------------------------
    # Normal ranges (từ đề + phân tích generator):
    #   memory_usage_bytes:   ~760M – 840M
    #   cpu_usage_percent:    ~22 – 43
    #   http_requests_per_sec: ~62 – 178
    #   http_p99_latency_ms:  ~30 – 69
    #   http_5xx_rate:        0 – 0.7
    #   jvm_gc_pause_ms_avg:  ~6 – 18
    #   queue_depth:          ~1 – 9
    #   upstream_timeout_rate: 0 – 0.3

    THRESHOLDS = {
        # memory_leak signals
        "memory_high_pct": 50,          # % of limit (normal ~40%)
        "memory_critical_pct": 75,
        "gc_pause_warn": 30,            # ms (normal max ~18)
        "gc_pause_critical": 100,
        "memory_trend_growth": 1.05,    # 5% growth first→second half

        # traffic_spike signals
        "rps_warn": 300,                # req/s (normal max ~178)
        "rps_critical": 600,
        "queue_warn": 30,               # (normal max ~9)
        "queue_critical": 100,
        "latency_warn": 200,            # ms (normal max ~69)

        # dependency_timeout signals
        "timeout_rate_warn": 3.0,       # % (normal max ~0.3)
        "timeout_rate_critical": 40,
        "error_rate_warn": 5.0,         # % (normal max ~0.7)
        "error_rate_critical": 20,
        "latency_timeout": 300,         # ms
    }

    WARMUP_TICKS = 10          # bỏ qua N ticks đầu để tránh false positive
    COOLDOWN_TICKS = 10        # tối thiểu N ticks giữa 2 alert cùng loại

    def __init__(self):
        self.history = {
            "memory_usage_bytes": deque(maxlen=WINDOW_SIZE),
            "cpu_usage_percent": deque(maxlen=WINDOW_SIZE),
            "http_requests_per_sec": deque(maxlen=WINDOW_SIZE),
            "http_p99_latency_ms": deque(maxlen=WINDOW_SIZE),
            "http_5xx_rate": deque(maxlen=WINDOW_SIZE),
            "jvm_gc_pause_ms_avg": deque(maxlen=WINDOW_SIZE),
            "queue_depth": deque(maxlen=WINDOW_SIZE),
            "upstream_timeout_rate": deque(maxlen=WINDOW_SIZE),
        }
        self.tick_count = 0
        self.alert_cooldown: dict[str, int] = {}
        self.total_alerts = 0

    # --- helpers -----------------------------------------------------------

    def _add_metrics(self, metrics: dict):
        for key in self.history:
            if key in metrics:
                self.history[key].append(metrics[key])
        self.tick_count += 1

    def _can_alert(self, fault_type: str) -> bool:
        if fault_type not in self.alert_cooldown:
            return True
        return self.tick_count - self.alert_cooldown[fault_type] >= self.COOLDOWN_TICKS

    def _record_alert(self, fault_type: str):
        self.alert_cooldown[fault_type] = self.tick_count
        self.total_alerts += 1

    def _memory_trending_up(self) -> bool:
        """So sánh trung bình nửa đầu vs nửa sau của window."""
        mem = list(self.history["memory_usage_bytes"])
        if len(mem) < 10:
            return False
        mid = len(mem) // 2
        first_half = statistics.mean(mem[:mid])
        second_half = statistics.mean(mem[mid:])
        return second_half > first_half * self.THRESHOLDS["memory_trend_growth"]

    # --- Detector cho từng loại fault --------------------------------------

    def _check_memory_leak(self, metrics: dict, logs: list) -> dict | None:
        if not self._can_alert("memory_leak"):
            return None

        T = self.THRESHOLDS
        mem = metrics["memory_usage_bytes"]
        mem_limit = metrics["memory_limit_bytes"]
        mem_pct = mem / mem_limit * 100
        gc = metrics["jvm_gc_pause_ms_avg"]

        signals = 0
        evidence = []

        if mem_pct > T["memory_high_pct"]:
            signals += 1
            evidence.append(f"memory at {mem_pct:.0f}%")

        if gc > T["gc_pause_warn"]:
            signals += 1
            evidence.append(f"GC pause {gc:.0f}ms")

        if self._memory_trending_up() and mem > 900_000_000:
            signals += 1
            evidence.append("memory trending up")

        # Log evidence: OutOfMemoryWarning
        for log in logs:
            if "OutOfMemory" in log.get("message", ""):
                signals += 2
                evidence.append("OOM warning in logs")
                break

        if signals >= 2:
            severity = "critical" if mem_pct > T["memory_critical_pct"] or gc > T["gc_pause_critical"] else "warning"
            self._record_alert("memory_leak")
            return {
                "type": "memory_leak",
                "severity": severity,
                "message": f"Memory leak detected: {', '.join(evidence)}",
            }
        return None

    def _check_traffic_spike(self, metrics: dict, logs: list) -> dict | None:
        if not self._can_alert("traffic_spike"):
            return None

        T = self.THRESHOLDS
        rps = metrics["http_requests_per_sec"]
        queue = metrics["queue_depth"]
        latency = metrics["http_p99_latency_ms"]

        signals = 0
        evidence = []

        if rps > T["rps_warn"]:
            signals += 1
            evidence.append(f"RPS={rps:.0f}")
        if rps > T["rps_critical"]:
            signals += 1

        if queue > T["queue_warn"]:
            signals += 1
            evidence.append(f"queue_depth={queue}")

        if latency > T["latency_warn"]:
            signals += 1
            evidence.append(f"p99_latency={latency:.0f}ms")

        # Log evidence: queue/overload
        for log in logs:
            msg = log.get("message", "")
            if "Queue depth high" in msg or "server overloaded" in msg:
                signals += 1
                evidence.append("overload in logs")
                break

        if signals >= 2:
            severity = "critical" if rps > T["rps_critical"] or queue > T["queue_critical"] else "warning"
            self._record_alert("traffic_spike")
            return {
                "type": "traffic_spike",
                "severity": severity,
                "message": f"Traffic spike detected: {', '.join(evidence)}",
            }
        return None

    def _check_dependency_timeout(self, metrics: dict, logs: list) -> dict | None:
        if not self._can_alert("dependency_timeout"):
            return None

        T = self.THRESHOLDS
        timeout_rate = metrics["upstream_timeout_rate"]
        error_rate = metrics["http_5xx_rate"]
        latency = metrics["http_p99_latency_ms"]

        signals = 0
        evidence = []

        if timeout_rate > T["timeout_rate_warn"]:
            signals += 1
            evidence.append(f"upstream_timeout={timeout_rate:.1f}%")
        if timeout_rate > T["timeout_rate_critical"]:
            signals += 1

        if error_rate > T["error_rate_warn"]:
            signals += 1
            evidence.append(f"5xx_rate={error_rate:.1f}%")

        if latency > T["latency_timeout"]:
            signals += 1
            evidence.append(f"p99_latency={latency:.0f}ms")

        # Log evidence: circuit breaker / timeout
        for log in logs:
            msg = log.get("message", "")
            if "Circuit breaker" in msg or "timeout" in msg.lower():
                signals += 1
                evidence.append("timeout/circuit-breaker in logs")
                break

        if signals >= 2:
            severity = "critical" if timeout_rate > T["timeout_rate_critical"] or error_rate > T["error_rate_critical"] else "warning"
            self._record_alert("dependency_timeout")
            return {
                "type": "dependency_timeout",
                "severity": severity,
                "message": f"Dependency timeout detected: {', '.join(evidence)}",
            }
        return None

    # --- public interface --------------------------------------------------

    def detect(self, metrics: dict, logs: list) -> list[dict]:
        """Nhận metrics + logs, trả về list alert (có thể rỗng)."""
        self._add_metrics(metrics)

        # Chờ warmup để có đủ baseline
        if self.tick_count < self.WARMUP_TICKS:
            return []

        alerts = []
        for checker in (
            self._check_memory_leak,
            self._check_traffic_spike,
            self._check_dependency_timeout,
        ):
            alert = checker(metrics, logs)
            if alert:
                alerts.append(alert)
        return alerts


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

detector = AnomalyDetector()


@app.post("/ingest")
async def ingest(request: Request):
    """Nhận payload từ stream_generator, chạy detection, ghi alert."""
    payload = await request.json()
    metrics = payload["metrics"]
    logs = payload.get("logs", [])
    timestamp = payload["timestamp"]

    # Chạy anomaly detection
    alerts = detector.detect(metrics, logs)

    # Ghi alert vào file
    for alert in alerts:
        alert["timestamp"] = timestamp
        with open(ALERTS_FILE, "a") as f:
            f.write(json.dumps(alert) + "\n")
        print(f"🚨 [ALERT] {alert['type']} | {alert['severity']} | {alert['message']}")

    # Log heartbeat mỗi 20 ticks
    if detector.tick_count % 20 == 0:
        print(
            f"📊 [PIPELINE] Processed {detector.tick_count} data points | "
            f"Total alerts fired: {detector.total_alerts}"
        )

    return {"status": "ok"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "ticks_processed": detector.tick_count,
        "total_alerts": detector.total_alerts,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("🔍 AIOps Anomaly Pipeline — Starting...")
    print(f"   Endpoint:    POST http://0.0.0.0:8000/ingest")
    print(f"   Alerts file: {ALERTS_FILE}")
    print(f"   Window size: {WINDOW_SIZE} ticks")
    print(f"   Warmup:      {AnomalyDetector.WARMUP_TICKS} ticks (no alerts)")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
