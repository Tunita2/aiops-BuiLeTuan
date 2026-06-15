# W3-D1 DESIGN — Bui Le Tuan

## 1. SLI choice cho frontend

**Chọn: Composite availability = (dom_ready < 3000ms AND no js_error AND no network_error) / total page loads.**

Frontend RUM log cung cấp 4 candidate signals. Từ 518,400 events trong 3-day baseline:

| Signal | Fail rate | Proportional với user pain? |
|--------|-----------|--------------------------|
| JS error rate | 0.90% (4,682 events) | ✅ Trực tiếp gây broken UX |
| Network error rate | 0.47% (2,433 events) | ✅ Page không load được |
| DOM ready > 3000ms | 0.025% (131 events) | ✅ Nhưng quá ít event → weak signal |
| DOM ready p99 = 1430ms | — | Chỉ phản ánh tail latency, không phải failure |

**Loại DOM ready time (raw latency) làm SLI primary** vì nó không binary — p50 = 405ms, p99 = 1430ms, p99.9 = 2329ms — rất ít event vượt 3000ms threshold. Dùng threshholded DOM ready (< 3000ms) kết hợp với js_error + network_error tạo composite SLI proportional hơn.

**Loại DOM ready p99 standalone** vì percentile metric khó convert thành "tỷ lệ event tốt / tổng event" — SLI cần dạng 0-1. Network error rate (0.47%) correlate cao với js_error nên đưa cả hai vào composite thay vì chọn riêng. Composite SLI catch được cả CDN slowdown (dom_ready spike) lẫn JS runtime crash (js_error) và connectivity failure (network_error) — bao phủ 3 loại user pain chính. Baseline composite success rate = 98.61% (baseline.json `frontend.success_rate`).

---

## 2. SLO target cho API

**Chọn 99.9% (3 nines), không chọn 99% hay 99.99%.**

Từ baseline.json, API success_rate = 97.63% (bao gồm cả incident data). Fail rate baseline = 0.35% (trong đó 5xx = 0.298%, 429 = 0.05%). Nếu loại bỏ 5 incident periods (chiếm ~155 phút có elevated fail), fail rate normal operation ước tính ~0.1% → success rate normal ~99.9%.

| SLO target | Error budget/tháng | Architecture (§3.2) | Fit? |
|-----------|-------------------|---------------------|------|
| 99% (2 nines) | 207,378 requests | 1 instance, manual recovery | Quá lỏng — cho phép 14 phút downtime/ngày, team không có incentive fix |
| **99.9% (3 nines)** | **20,737 requests (43 min)** | Multi-instance, LB, auto-failover | ✅ Khớp architecture 4-instance FastAPI + LB |
| 99.99% (4 nines) | 2,074 requests (4.3 min) | Multi-AZ, automated runbook, 24/7 on-call | Quá chặt — miss ngay tháng đầu với current infra |

99.9% cho budget 43 phút/tháng — đủ cho 2-3 incident nhỏ nhưng buộc phải fix incident lớn. Architecture hiện tại (4-instance FastAPI + LB) đáp ứng được tier này. 99.99% yêu cầu Multi-AZ + 24/7 on-call, không phù hợp với e-commerce scale hiện tại (~8 req/s average).

---

## 3. Latency threshold p99

**Chọn threshold 500ms cho SLI "good" request.**

Phân phối latency 3-day từ access_log.jsonl (2,073,780 requests):

| Percentile | Latency |
|-----------|---------|
| p50 | 45ms |
| p90 | 86ms |
| p95 | 104ms |
| p99 | 156ms |
| p99.5 | 190ms |
| p99.9 | 394ms |

p99 = 156ms nghĩa là 99% request hoàn thành trong 156ms — hệ thống normally rất nhanh. Threshold candidate:

- **200ms**: Quá tight — trong incident, latency spike lên 500-1000ms là common. Cut 200ms sẽ đẩy nhiều "slow but still OK" request vào fail bucket → SLI quá sensitive, noisy.
- **500ms**: Sweet spot — normal p99.9 = 394ms < 500ms, nên trong operation bình thường gần 100% request pass. Khi có incident (latency × 3-10x), request >500ms là genuinely degraded user experience. E-commerce checkout >500ms gây user drop-off.
- **1000ms**: Quá lỏng — user đã chờ 1 giây là frustrated. Threshold này miss moderate degradation.

Chọn 500ms vì nó ở "gap" giữa normal tail (p99.9 = 394ms) và incident-degraded traffic. Compute_baseline.py dùng cùng threshold 500ms, confirm success_rate API = 97.63% (baseline.json).

---

## 4. 4xx exclusion

**Loại tất cả 4xx (trừ 429) ra khỏi error count.**

Từ analysis data, API có 2.01% 4xx rate (41,712 events / 2,073,780 total), phân bố đều trên mọi endpoint:

| Path | 4xx rate | 4xx count / total |
|------|----------|------------------|
| /api/cart | 2.04% | 8,467 / 415,386 |
| /api/checkout | 2.01% | 8,334 / 414,342 |
| /api/orders | 2.02% | 8,360 / 414,811 |
| /api/products | 2.02% | 8,352 / 414,335 |
| /api/user | 1.98% | 8,199 / 414,906 |

Tất cả endpoint đều có 4xx rate ~2% — đây là pattern đặc trưng của **bot/scraper traffic + user input validation error** (400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found). Nếu là system error, ta sẽ thấy spike trên 1-2 endpoint cụ thể, không phải uniform 2% trên tất cả 5 endpoint.

**429 (Rate Limited) PHẢI đếm vào fail** vì: khi system trả 429, nó chủ động reject legitimate user traffic do capacity constraint — đây là system failure, không phải user error.

Nếu đếm 4xx vào fail: fail_rate = 2.01% + 0.30% + 0.05% ≈ 2.36% → SLI = 97.64% → SLO 99.9% miss liên tục vì bot traffic — anti-pattern "SLI bị bot/scraper kéo xuống" (§10). Loại 4xx giữ SLI phản ánh đúng system health: fail_rate = 0.35% (5xx + 429 only).

---

## 5. MWMBR tuning

**Giữ nguyên Google default: tier 1 = 14.4, tier 2 = 6, tier 3 = 1.**

Từ validation_report.json:
- Static baseline (error_rate > 0.5% for 5m): fired = 22, tp = 3, fp = 19, mttd_p50 = 0s
- MWMBR (14.4, 6, 1): fired = 3, tp = 3, fp = **0**, mttd_p50 = 60s
- **noise_reduction_pct = 86.4%** (vượt xa threshold 70%)
- **mttd_delta = 60s** (đúng bằng ngưỡng cho phép 60s)
- **fn = 0** — không miss incident nào
- **verdict = pass**

Lý do giữ Google default thay vì custom tune:
1. **FP = 0** — zero false positive nghĩa là không cần hạ threshold để giảm noise (đã tối ưu).
2. **FN = 0** — bắt được cả 3 API incidents (incident #1: 8 phút tier1, incident #3: 12 phút tier1, incident #5: 20 phút tier2). Nếu tăng threshold (ví dụ tier1 = 20), risk miss incident ngắn.
3. **MTTD delta = 60s** — sát ngưỡng accept. Nếu tune tăng threshold → MTTD tăng → fail acceptance.
4. Noise reduction 86.4% rất tốt — cải thiện 4.8× so với static rule.

Google default đã well-calibrated cho scenario: SLO 99.9% + 30-day window + moderate traffic (~8 req/s). Tune chỉ cần thiết khi: (a) traffic rất thấp (noise trong short window) hoặc (b) SLO rất tight (99.99%+). Cả hai đều không áp dụng ở đây.
