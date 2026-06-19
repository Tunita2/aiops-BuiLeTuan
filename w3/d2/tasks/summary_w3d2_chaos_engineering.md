# Tóm tắt bài học: W3-D2 — Chaos Engineering — Validate AIOps Pipeline

---

## 1. Bài này dạy điều gì trong 1 câu?

**Bài này dạy cách chủ động "phá" hệ thống phân tán một cách có kiểm soát (Chaos Engineering) để kiểm chứng xem pipeline AIOps (detector → correlator → RCA) có thực sự phát hiện và chẩn đoán đúng lỗi hay không.**

---

## 2. Vấn đề thực tế trước khi học bài này là gì?

Bạn đã xây xong pipeline AIOps ở W1 + W2 (detector phát hiện anomaly, correlator gom nhóm, RCA chỉ ra root cause). Nhưng **bạn chưa bao giờ chứng minh nó thực sự hoạt động đúng khi lỗi thật xảy ra**:

- Pipeline có phát hiện được lỗi latency tăng 500ms ở payment-svc không? → **Không biết.**
- Pipeline có chẩn đoán đúng service gốc gây lỗi không? → **Không biết.**
- Pipeline có bị "câm" khi monitoring stack chính nó cũng bị lỗi không? → **Không biết.**

Nói cách khác: **bạn có một pipeline chưa được kiểm thử bằng lỗi thật**, giống như có hệ thống báo cháy nhưng chưa bao giờ thử đốt lửa để xem nó có kêu không.

---

## 3. Các khái niệm chính

### 3.1 Chaos Engineering

| | |
|---|---|
| **Thuật ngữ** | Chaos Engineering |
| **Định nghĩa** | Kỷ luật thực nghiệm: chủ động inject lỗi vào hệ thống phân tán để phát hiện điểm yếu *trước khi* lỗi xảy ra tự nhiên ở production |
| **Ví dụ** | Dùng Pumba inject latency 500ms vào payment-svc, xem pipeline có phát hiện anomaly trong 60 giây không |
| **Lỗi hiểu sai** | ❌ "Chaos = phá hệ thống ngẫu nhiên" → ✅ Chaos = thí nghiệm có giả thuyết, có đo lường, có rollback, có blast radius |

### 3.2 Steady-state behavior (Trạng thái ổn định)

| | |
|---|---|
| **Thuật ngữ** | Steady-state behavior |
| **Định nghĩa** | Các chỉ số đo được khi hệ thống hoạt động bình thường, dùng làm mốc so sánh trước/sau inject lỗi |
| **Ví dụ** | `order_success_rate ≥ 99.5%`, `checkout_p99 ≤ 800ms` |
| **Lỗi hiểu sai** | ❌ "Steady-state = hệ thống sống" → ✅ Steady-state = các chỉ số cụ thể, đo được, có ngưỡng rõ ràng |

### 3.3 Blast radius (Phạm vi ảnh hưởng)

| | |
|---|---|
| **Thuật ngữ** | Blast radius |
| **Định nghĩa** | Phạm vi hệ thống bị ảnh hưởng bởi thí nghiệm chaos — bắt đầu nhỏ, mở rộng dần |
| **Ví dụ** | Stage 1: 1 container dev → Stage 3: 1 instance prod, 10% traffic → Stage 5: all regions, 100% traffic |
| **Lỗi hiểu sai** | ❌ "Test thẳng production cho nhanh" → ✅ Phải leo thang từ dev → staging → canary → region → global. Không nhảy cóc |

### 3.4 Fault categories (4 loại lỗi inject)

| | |
|---|---|
| **Thuật ngữ** | Network / Resource / Application / State faults |
| **Định nghĩa** | 4 lớp lỗi có thể inject: mạng (latency, packet loss), tài nguyên (CPU, RAM, disk), ứng dụng (kill pod, inject 500), trạng thái (clock skew, config hỏng) |
| **Ví dụ** | Network: `tc netem delay 500ms`; Resource: `stress-ng --cpu 4 --cpu-load 90`; App: `kubectl delete pod`; State: `libfaketime +60s` |
| **Lỗi hiểu sai** | ❌ "Chaos chỉ là kill pod" → ✅ Có 4 lớp lỗi khác nhau, mỗi lớp test một khía cạnh khác của hệ thống |

### 3.5 Confusion matrix cho pipeline

| | |
|---|---|
| **Thuật ngữ** | TP, FP, FN, TN trong ngữ cảnh AIOps |
| **Định nghĩa** | TP = pipeline phát hiện đúng lỗi đã inject; FN = lỗi inject nhưng pipeline im lặng (miss); FP = không inject lỗi nhưng pipeline báo động (false alarm); TN = không lỗi, pipeline im lặng (đúng) |
| **Ví dụ** | Inject latency 500ms → pipeline alert → TP. Inject disk fill → pipeline im lặng → FN |
| **Lỗi hiểu sai** | ❌ "Pipeline chạy mà không có false alarm là tốt" → ✅ Phải đo cả recall (bao nhiêu lỗi pipeline bắt được) lẫn precision |

### 3.6 Synthetic probe (Đầu dò tổng hợp bên ngoài)

| | |
|---|---|
| **Thuật ngữ** | External synthetic probe |
| **Định nghĩa** | Một process chạy bên ngoài cluster, gọi endpoint như user thật, ghi pass/fail — đại diện cho trải nghiệm user thực tế |
| **Ví dụ** | Script bash mỗi 5s curl `http://localhost:8080/checkout/health`, ghi `pass 45ms` hoặc `fail 503 2100ms` |
| **Lỗi hiểu sai** | ❌ "Prometheus scrape bên trong đủ rồi" → ✅ Internal metric có thể bị đánh lừa (200 nhưng body sai, cache cũ). External probe đo đúng cái user thấy |

### 3.7 RCA accuracy

| | |
|---|---|
| **Thuật ngữ** | RCA accuracy (Root Cause Analysis accuracy) |
| **Định nghĩa** | Tỷ lệ RCA chỉ đúng service gốc gây lỗi trong số các lỗi đã phát hiện |
| **Ví dụ** | Inject lỗi payment-svc → RCA nói "root cause = payment-svc" → RCA_correct. Nếu RCA nói "checkout-svc" (vì retry storm) → RCA_wrong |
| **Lỗi hiểu sai** | ❌ "Service nào có nhiều alert nhất = root cause" → ✅ Service "ồn" nhất thường là downstream bị ảnh hưởng, không phải gốc |

---

## 4. Một ví dụ xuyên suốt từ đầu đến cuối bài

> **Kịch bản: Kiểm tra pipeline AIOps với lỗi latency ở payment-svc**

**Bước 1 — Xác định steady-state:**
- Chạy stack 10 service, đợi healthy
- Capture baseline 5 phút: `order_success_rate = 99.8%`, `checkout_p99 = 320ms`
- Chạy synthetic probe bên ngoài: pass-rate = 100% trong 60s → OK

**Bước 2 — Viết hypothesis:**
```yaml
name: "payment_latency_500ms"
hypothesis: |
  Steady-state: order_success_rate ≥ 99.5%, checkout_p99 ≤ 800ms.
  Khi inject latency +500ms vào payment-svc, pipeline phải:
  - Detect anomaly trong ≤ 60s
  - RCA chỉ đúng payment-svc
  - order_success_rate không giảm quá 5%
blast_radius:
  target: 1 instance payment-svc
  duration: 60s
rollback:
  automatic: true
  trigger: order_success_rate < 90%
  method: tc qdisc del (xóa netem rule)
measurement:
  metrics: [order_success_rate, checkout_p99, payment_retry_count]
```

**Bước 3 — Inject lỗi:**
```bash
pumba netem --duration 60s --tc-image gaiadocker/iproute2 \
  delay --time 500 --jitter 100 payment-svc
```

**Bước 4 — Đo kết quả:**
- Pipeline fire alert lúc `t + 28s` → **TP** (detected), MTTD = 28s
- RCA output: `{root_service: "payment-svc", confidence: 0.87}` → **RCA_correct**
- Synthetic probe: pass-rate giảm từ 100% → 85% trong 60s → user bị ảnh hưởng thật
- Sau rollback (t + 60s): pass-rate trở lại 99% trong 90s → hệ thống recovered

**Bước 5 — Ghi vào scoreboard:**

| # | name | detected | mttd | rca_service | rca_correct |
|---|------|----------|------|-------------|-------------|
| 1 | payment_latency | Y | 28s | payment-svc | Y |

**Bước 6 — Lặp lại cho 9 thí nghiệm còn lại**, cooldown 120s giữa mỗi thí nghiệm.

**Bước 7 — Tổng kết:**
- Detected 8/10 → recall = 0.80 ✅ (≥ 0.70)
- RCA correct 6/8 → rca_accuracy = 0.75 ✅ (≥ 0.70)
- False alarms = 0 ✅ (≤ 1)
- Gaps: experiment #7 (log-collector disk fill) không detect → pipeline thiếu meta-monitoring

---

## 5. Phần bài tập yêu cầu làm gì?

Bài tập gồm **7 bước**:

| Bước | Yêu cầu | Output |
|------|---------|--------|
| 1 | Capture baseline 5 phút + chạy synthetic probe | `baseline.json` + `probe.log` |
| 2 | Thiết kế 10 thí nghiệm chaos (catalog cho sẵn) | Bảng 10 experiments |
| 3 | Điền `experiments.yaml` (10 entries, mỗi entry 5 field) | `experiments.yaml` |
| 4 | Code `chaos_runner.py` — implement 2 hàm: `build_inject_cmd()` + `print_scoreboard()` | `chaos_runner.py` |
| 5 | Chạy 10 thí nghiệm, in scoreboard | `chaos_results.json` + scoreboard stdout |
| 6 | Viết report phân tích chi tiết | `chaos_report.md` (4 sections bắt buộc) |
| 7 | Viết submission | `SUBMIT.md` (4 sections) |

**Tiêu chí đạt:**
- Detected ≥ 7/10 (recall ≥ 70%)
- RCA correct ≥ 5/detected (≈70% accuracy)
- False alarm ≤ 1

---

## 6. Những công thức hoặc rule quan trọng

### Công thức đo pipeline

```
precision = TP / (TP + FP)        → "Khi pipeline báo động, bao nhiêu % là đúng?"
recall    = TP / (TP + FN)        → "Trong tất cả lỗi thật, pipeline bắt được bao nhiêu %?"
MTTD      = mean(alert_time - inject_time)  → "Trung bình mất bao lâu để phát hiện?"
rca_accuracy = RCA_correct / TP   → "Trong số lỗi detect được, bao nhiêu % chỉ đúng root cause?"
```

### Ví dụ số

- 10 thí nghiệm, pipeline detect 8, miss 2. Baseline window có 1 false alarm.
- TP = 8, FN = 2, FP = 1, TN = baseline OK
- **precision = 8 / (8+1) = 0.89**
- **recall = 8 / (8+2) = 0.80**
- RCA correct 6/8 → **rca_accuracy = 0.75**

### 5 nguyên tắc cốt lõi (Rules)

1. **Hypothesis trước, inject sau** — phải định nghĩa "OK" bằng số trước khi phá
2. **Vary real-world events** — inject lỗi giống thật: crash, latency, timeout
3. **Run in production** — staging không tái hiện được scale thật
4. **Automate continuously** — chaos thủ công 1 lần/quý = không đáng tin
5. **Minimize blast radius** — bắt đầu nhỏ (1 instance, 1% traffic), mở rộng dần

### Rule cooldown

- **120 giây** giữa mỗi thí nghiệm — đợi hệ thống trở về baseline trước khi inject tiếp

---

## 7. Những anti-pattern cần tránh

| ❌ Anti-pattern | 💥 Hậu quả |
|---|---|
| Inject lỗi **không có hypothesis** | Phá hệ thống, chẳng học được gì |
| Inject thẳng vào **prod trước khi qua staging** | Gây outage thật, không phải chaos |
| **Quên rollback script** | Lỗi dính luôn sau thí nghiệm, ops phải fix tay |
| Đo lường chỉ là **"hệ thống còn sống"** | Bỏ lọt silent failure, degradation từ từ |
| **Nhảy cóc** blast radius (dev → prod luôn) | Stage 1 fail → stage 5 phá sập prod |
| Chaos **1 tháng/lần** thay vì liên tục | 30 ngày drift → bug đã vào prod trước khi chaos phát hiện |
| Chỉ inject **1 service**, không kết hợp | Outage thật thường multi-fault (VD: Roblox = streaming + BoltDB) |
| **Không version** experiment config | Không tái hiện được, không debug được run bị flaky |

---

## 8. Nếu tôi phải giải thích lại cho người khác trong 2 phút

> **"Chaos Engineering là cách bạn CHỦ ĐỘNG phá hệ thống để tìm lỗi TRƯỚC KHI user gặp."**
>
> Ở bài này, mình áp dụng nó để **kiểm tra pipeline AIOps** — cái pipeline detect anomaly, gom nhóm sự cố, và chỉ ra root cause mà mình xây ở tuần 1, 2.
>
> Quy trình gồm 3 bước:
> 1. **Đo baseline** — ghi lại hệ thống bình thường trông như thế nào (success rate 99.5%, latency p99 ≤ 800ms). Đồng thời chạy **synthetic probe** bên ngoài cluster để có tín hiệu độc lập.
> 2. **Inject lỗi có kiểm soát** — dùng tool như Pumba, Toxiproxy, Chaos Mesh để inject 10 loại lỗi khác nhau (latency, kill pod, CPU stress, partition, clock skew...). Mỗi lỗi phải có **hypothesis** rõ ràng, **blast radius** nhỏ, và **rollback** tự động.
> 3. **Đo kết quả bằng confusion matrix** — pipeline có detect lỗi không (TP/FN)? RCA có chỉ đúng service gốc không? Mất bao lâu (MTTD)?
>
> Mục tiêu: **recall ≥ 70%, RCA accuracy ≥ 70%, false alarm ≤ 1**. Nếu không đạt → ghi gap analysis, KHÔNG được cheat bằng cách tune pipeline cho pass.
>
> Bài học lớn nhất: **pipeline AIOps chưa được test bằng lỗi thật = chưa biết nó có hoạt động không.** Chaos Engineering là cách duy nhất để chứng minh.

---

## 9. 5 câu hỏi tự kiểm tra

### Câu 1
**Chaos Engineering khác gì load testing và penetration testing?**

> ✅ Load test kiểm tra hệ thống chịu được bao nhiêu tải. Pentest tìm lỗ hổng bảo mật. Chaos Engineering tìm **điểm yếu reliability do tương tác giữa các component** — loại lỗi chỉ xuất hiện khi nhiều service tương tác trong hệ thống phân tán.

### Câu 2
**Tại sao cần synthetic probe bên ngoài cluster thay vì chỉ dùng Prometheus bên trong?**

> ✅ Internal metric có thể bị đánh lừa: service trả 200 nhưng body sai, cache cũ, LB/ingress/DNS lỗi mà Prometheus không thấy. External probe đo chính xác cái user nhìn thấy — là tín hiệu steady-state đúng nhất theo nguyên tắc #1 của Chaos Engineering.

### Câu 3
**Trong retry-storm scenario (experiment #10), tại sao RCA KHÔNG được chỉ checkout-svc là root cause?**

> ✅ Vì checkout-svc chỉ "ồn" (retry 10 lần → 10 alert) nhưng nó là downstream. Root cause thật là payment-svc (bị inject 500 error). RCA đúng phải dùng topology-aware (upstream trước downstream) + temporal-causal (service nào drift trước).

### Câu 4
**Nếu thí nghiệm chaos ở stage 1 (dev) fail, bạn có nên chạy stage 3 (prod canary) không?**

> ✅ **KHÔNG.** Phải fix lỗi trước, retry stage 1 đến khi pass, rồi mới leo lên stage 2, 3. Nhảy cóc blast radius = anti-pattern, có thể gây sập production thật.

### Câu 5
**Cho scoreboard: 10 thí nghiệm, detect 6, miss 4, RCA correct 4/6, false alarm 2 trong baseline. Tính precision, recall, rca_accuracy. Có đạt acceptance không?**

> ✅ TP = 6, FN = 4, FP = 2
> - precision = 6/(6+2) = **0.75**
> - recall = 6/(6+4) = **0.60** ❌ (cần ≥ 0.70)
> - rca_accuracy = 4/6 = **0.67** ❌ (cần ≥ 5/6 ≈ 0.71, tức ≥ 5 cái đúng)
> - false alarm = 2 ❌ (cần ≤ 1)
> - **Verdict: KHÔNG ĐẠT** — cả 3 tiêu chí đều fail. Cần ghi gap analysis, không được tune pipeline cho pass.
