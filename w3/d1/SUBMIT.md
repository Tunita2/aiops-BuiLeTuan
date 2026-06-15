# W3-D1 Submission — Bui Le Tuan

## 3 thứ tôi học được

1. **SLI phải proportional với user pain, không phải system metric.** CPU ở 80% không đồng nghĩa user khổ — nó chỉ là saturation signal. Ngược lại, latency p99 = 5s mới phản ánh đúng trải nghiệm user. Bài học cốt lõi: SLI = user happiness metric, đo từ phía user (load balancer log, RUM probe), không phải phía server. Trong bài lab, chọn composite SLI cho frontend (dom_ready + js_error + network_error) thay vì chỉ 1 metric đơn lẻ.

2. **Error budget convert abstract SLO thành actionable number.** SLO 99.9% nghe trừu tượng, nhưng khi convert thành "20,737 request fail/tháng" hay "43 phút downtime", team có thể trade-off cụ thể: "incident này đã đốt 8 phút / 43 phút budget → vẫn ok" vs "incident đốt 30 phút → phải freeze release tuần sau." Budget làm cho SLO trở thành công cụ negotiation thay vì chỉ là con số trên dashboard.

3. **Multi-Window Multi-Burn-Rate giải quyết dilemma single-window.** Window ngắn (1m) thì noisy (FP = 19 trong lab), window dài (6h) thì chậm detect + dính lâu sau incident. MWMBR dùng AND condition giữa long window (confirm magnitude) và short window (confirm "đang xảy ra") — kết quả: FP = 0, noise reduction 86.4%, alert auto-recover trong ~5 phút sau incident hết. Đây là elegant solution cho bài toán mà single threshold không giải được.

## 1 thứ vẫn chưa rõ

**Cách handle SLI cho multi-region deployment khi latency khác nhau giữa regions.** Trong lab chỉ có single-region, mọi request đo từ 1 vantage point. Trong production multi-region, p99 latency từ US sẽ khác EU sẽ khác APAC. Nếu aggregate thành 1 SLI global thì region chậm bị ẩn bởi region nhanh. Nếu tách per-region thì có quá nhiều SLO phải track. Chưa rõ best practice cho trường hợp này — có nên dùng weighted SLI theo traffic proportion mỗi region, hay tạo per-region SLO riêng?

## 1 trade-off trong SLO decision của tôi mà tôi không chắc

**API SLO 99.9% vs 99.95%.** Baseline fail rate 0.35% bao gồm cả 5 incidents. Ước tính normal operation fail rate ~0.1%, tức success rate ~99.9%. Chọn SLO = 99.9% nghĩa là target gần bằng hiện tại, cho buffer rất mỏng (43 phút/tháng). Nếu chọn 99.95% thì budget chỉ còn ~21 phút — chặt hơn nhưng buộc team invest mạnh vào reliability (auto-failover, circuit breaker). 99.9% có thể "quá dễ" nếu normal operation đã đạt, nhưng 99.95% risk miss SLO tháng đầu và team mất trust vào SLO process. Tôi chọn 99.9% theo nguyên tắc "SLA < SLO < hiện tại" nhưng khoảng cách giữa SLO và hiện tại rất hẹp.

## Validation report

- noise_reduction_pct: **86.4%**
- mttd_delta_s: **60s**
- false_negative: **0**
- verdict: **pass**
