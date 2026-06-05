# Detection Approach — DESIGN.md

## Approach tôi dùng

**Multi-Signal Threshold + Sliding Window Correlation**

Kết hợp static thresholds (dựa trên normal ranges từ đề bài) với sliding window để phát hiện xu hướng (trend detection), cùng log correlation để tăng độ chính xác.

## Tại sao chọn approach này

1. **Phù hợp streaming**: Không cần lưu toàn bộ lịch sử — chỉ giữ 20 data points gần nhất trong sliding window (O(1) memory).
2. **Phát hiện nhanh**: Static thresholds cho phép phát hiện ngay khi metric vượt ngưỡng, không cần đợi tích lũy dữ liệu lâu.
3. **Tránh false positive**: Dùng multi-signal correlation — cần ít nhất 2 signals đồng thời mới fire alert. Một metric bất thường đơn lẻ do noise sẽ không kích hoạt.
4. **Phân biệt fault type**: Mỗi loại fault có bộ signals riêng biệt, pipeline kiểm tra từng loại độc lập.

## Cách hoạt động

### Flow xử lý mỗi data point

```
POST /ingest
    ↓
Thêm metrics vào sliding window (deque maxlen=20)
    ↓
Kiểm tra warmup (bỏ qua 10 ticks đầu)
    ↓
Chạy 3 detector song song:
    ├── check_memory_leak()
    ├── check_traffic_spike()
    └── check_dependency_timeout()
    ↓
Nếu có alert → ghi vào alerts.jsonl
```

### Logic phát hiện từng loại fault

#### 1. Memory Leak
- **Signal 1**: `memory_usage_bytes` > 50% limit (1GB) — bình thường chỉ ~40%
- **Signal 2**: `jvm_gc_pause_ms_avg` > 30ms — bình thường 8–18ms
- **Signal 3**: Memory trending up (trung bình nửa sau window > 5% so với nửa đầu)
- **Signal 4** (×2): Log chứa "OutOfMemoryWarning"
- **Cần ≥ 2 signals** để fire alert

#### 2. Traffic Spike
- **Signal 1**: `http_requests_per_sec` > 300 (bình thường max ~178)
- **Signal 2** (bonus): RPS > 600 → thêm 1 signal nữa
- **Signal 3**: `queue_depth` > 30 (bình thường max ~9)
- **Signal 4**: `http_p99_latency_ms` > 200ms (bình thường max ~69)
- **Signal 5**: Log chứa "Queue depth high" hoặc "server overloaded"
- **Cần ≥ 2 signals** để fire alert

#### 3. Dependency Timeout
- **Signal 1**: `upstream_timeout_rate` > 3.0% (bình thường max ~0.3%)
- **Signal 2** (bonus): timeout_rate > 40% → thêm 1 signal nữa
- **Signal 3**: `http_5xx_rate` > 5.0% (bình thường max ~0.7%)
- **Signal 4**: `http_p99_latency_ms` > 300ms
- **Signal 5**: Log chứa "Circuit breaker" hoặc "timeout"
- **Cần ≥ 2 signals** để fire alert

### Cơ chế chống false positive

1. **Warmup phase**: Bỏ qua 10 ticks đầu tiên — chờ có đủ dữ liệu baseline
2. **Conservative thresholds**: Tất cả ngưỡng đều đặt CAO HƠN giới hạn max của normal range, đảm bảo noise bình thường không trigger
3. **Multi-signal requirement**: Cần ≥ 2 signals đồng thời — loại bỏ trường hợp chỉ 1 metric nhảy do noise
4. **Cooldown**: Sau khi fire alert, đợi 10 ticks trước khi fire alert cùng loại lần nữa

## Parameters tôi chọn

| Parameter | Giá trị | Lý do |
|-----------|---------|-------|
| `WINDOW_SIZE` | 20 ticks | ~60s real-time (ở speed=10), đủ để detect trend mà không quá dài |
| `WARMUP_TICKS` | 10 ticks | ~30s real-time, đủ để có baseline ổn định |
| `COOLDOWN_TICKS` | 10 ticks | Tránh spam alert cùng loại, nhưng vẫn cập nhật severity |
| `min_signals` | 2 | Cân bằng giữa sensitivity và specificity |
| Severity escalation | warning → critical | Dựa trên mức độ nghiêm trọng của metrics (VD: memory > 75% = critical) |

## Cải thiện nếu có thêm thời gian

1. **Exponential Moving Average (EMA)** thay vì static thresholds — tự adapt theo baseline thực tế, phù hợp hơn khi traffic có seasonal pattern phức tạp.
2. **Z-score detection** — tính z-score trên sliding window, phát hiện outlier chính xác hơn trong trường hợp distribution thay đổi.
3. **Correlation matrix** — theo dõi correlation giữa các metrics (VD: memory↑ + gc↑ + cpu↑ cùng lúc = memory leak với confidence cao hơn).
4. **Hysteresis** — alert chỉ clear khi metrics quay về normal range một thời gian, tránh flapping.
5. **Dashboard** — thêm endpoint `/dashboard` hiển thị metrics real-time và alert history.
