# W2-D1: Alert Correlation — Submission

**Author:** Bui Le Tuan  
**Date:** 2026-06-08

---

## 7.3 — Design Decisions

### 1. Chọn gap_sec bao nhiêu, vì sao?

Tôi chọn **gap_sec = 120 giây (2 phút)**. Đây là sweet spot được khuyến nghị cho production system. Lý do:

- **Quá nhỏ (30s):** Incident kéo dài bị tách thành nhiều session nhỏ vụn vặt. Ví dụ: payment-svc cascade kéo dài 6.5 phút sẽ bị cắt thành 5+ session nếu gap_sec=30, làm mất ngữ cảnh tổng thể.
- **Quá lớn (600s):** Hai incident hoàn toàn khác nhau nhưng cách nhau dưới 10 phút sẽ bị gộp nhầm, tạo false positive. Ví dụ: payment-svc crash lúc 09:42 và một sự cố inventory lúc 09:50 sẽ bị coi là cùng incident.
- **120s là điểm cân bằng:** Đủ lớn để giữ nguyên vẹn một incident cascade (các alert trong cascade thường đến liên tục với gap < 60s), nhưng đủ nhỏ để tách biệt các incident không liên quan.

Với dataset hiện tại, gap lớn nhất giữa 2 alert liên tiếp chỉ là 49 giây, nên gap_sec=120 tạo ra 1 session duy nhất — đúng vì toàn bộ alert đều thuộc cùng một đợt sự cố payment-svc pool exhaustion.

### 2. Chọn max_hop bao nhiêu, vì sao?

Tôi chọn **max_hop = 2**. Lý do:

- **max_hop = 1:** Chỉ gộp service có kết nối trực tiếp. Quá chặt — bỏ sót cascade qua 1 service trung gian (ví dụ: edge-lb → checkout-svc → payment-svc, edge-lb cách payment 2 hop).
- **max_hop = 2:** Bao phủ đúng cascade chain phổ biến trong microservices (caller → intermediate → callee). Trong dataset, payment-svc cascade ảnh hưởng edge-lb (2 hops), cart-svc (2 hops), notification-svc (2 hops) — đều nằm trong phạm vi max_hop=2.
- **max_hop = 3+:** Quá rộng — gom nhầm service không liên quan.

**Cải tiến quan trọng:** Phiên bản ban đầu dùng undirected graph + Union-Find transitive gây over-clustering (20 alert → 1 cluster). Phiên bản cải tiến dùng **Directed-Propagation**: chỉ kiểm tra max_hop trên **alert subgraph** (đồ thị con chỉ chứa service có alert), tránh kéo service ở rìa hệ thống (recommender, search) qua chuỗi service trung gian không có alert (catalog-svc).

### 3. Alert nào bị "miss" (false positive)?

Sau khi nâng cấp sang **Directed-Propagation topology grouping**, cả hai alert noise đã được tách thành cluster riêng:

- **a-0013 (recommender-svc, cpu_utilization, warn):** Note ghi rõ *"unrelated — concurrent batch retrain"*. Đây là alert độc lập do recommender-svc đang chạy batch retrain ML model, KHÔNG liên quan đến payment cascade. Trong alert subgraph, recommender-svc hoàn toàn cô lập (in_degree=0, out_degree=0) vì catalog-svc (service trung gian) không có alert → **tách riêng thành cluster c-000-001**.

- **a-0016 (search-svc, catalog_db_query_time_ms, warn):** Note ghi *"noise — independent slow query"*. Search-svc truy vấn catalog-db chậm do nguyên nhân riêng, KHÔNG phải do payment cascade. Trong alert subgraph, search-svc là sink (out_degree=0), chỉ có edge-lb gọi tới nhưng edge-lb thuộc tier 'edge' nên KHÔNG đủ điều kiện gộp → **tách riêng thành cluster c-000-002**.

### 4. Nếu có 10,000 alert, code chậm ở đâu?

Bottleneck chính là **topology_group()** — cụ thể là vòng lặp nested O(S²) tính `nx.shortest_path_length()` cho mọi cặp service. Với 10,000 alert có thể bao phủ hàng trăm service, số phép tính path sẽ tăng theo bình phương. Mỗi `shortest_path_length` trên graph lớn cũng tốn O(V+E) cho BFS. Tổng cộng: **O(S² × (V+E))** — rất chậm khi S (số service có alert) lớn.

Cách khắc phục: Pre-compute all-pairs shortest path bằng `nx.all_pairs_shortest_path_length()` một lần duy nhất, sau đó lookup O(1). Hoặc dùng BFS giới hạn (chỉ BFS đến max_hop) thay vì tính exact shortest path.

---

## 8. EOD Checkpoint

### 8.1 Vì sao fingerprint không include timestamp hay value?

Fingerprint chỉ bao gồm `service|metric|severity` — 3 trường **cố định** định danh "loại alert nào". Nếu include timestamp hoặc value:

- **Include timestamp:** Mỗi lần alert fire, timestamp khác nhau → fingerprint khác → KHÔNG CÓ alert nào bị coi là trùng → dedup hoàn toàn vô dụng. Ví dụ: `payment-svc|latency_p99_ms|crit` fire 3 lần (a-0003, a-0008, a-0015) với 3 timestamp khác nhau → 3 fingerprint khác nhau → 3 "alert mới" thay vì 1 alert lặp 3 lần.
- **Include value:** Tương tự, value luôn thay đổi (1840, 1840, 1840 — hoặc 0.85 → 0.99 → 1.00 cho db_pool_ratio) → dedup miss.

### 8.2 Sự khác biệt giữa "duplicate" và "correlated" alert?

- **Duplicate:** Hai alert có **cùng fingerprint** (cùng service, metric, severity). Ví dụ: a-0003, a-0008, a-0015 đều là `payment-svc|latency_p99_ms|crit` — cùng loại alert fire lặp lại. Layer 1 (Dedup) xử lý.

- **Correlated:** Hai alert **khác fingerprint** nhưng có mối liên hệ nhân quả (cùng root cause). Ví dụ: a-0003 (`payment-svc|latency_p99_ms|crit`) và a-0006 (`checkout-svc|downstream_payment_error_rate|crit`) — khác service, khác metric, nhưng cùng do payment-svc pool exhaustion gây ra. Layer 2+3 (Time-Window + Topology) xử lý.

### 8.3 gap_sec = 30 vs gap_sec = 600

- **gap_sec = 30:** Dataset bị tách thành **5 sessions**. Incident payment cascade kéo dài 6.5 phút bị cắt vụn. Alert a-0018 (payment error_rate crit) nằm riêng 1 session với a-0019, a-0020 — mất liên kết với giai đoạn đầu của cascade (a-0001 → a-0012). Engineer phải xem 5 cluster riêng biệt, khó nhìn thấy bức tranh tổng thể.

- **gap_sec = 600:** Tất cả 20 alert gộp vào 1 session (vì gap max chỉ 49s << 600s). Nếu có thêm incident khác xảy ra trong 10 phút tiếp theo, nó cũng bị gộp nhầm vào session này → false correlation, gây nhiễu cho triage.

### 8.4 Recommender-svc có bị gom vào cluster chính không?

**KHÔNG — nhờ Directed-Propagation topology grouping.**

Correlator phiên bản nâng cấp tách recommender-svc ra thành cluster riêng (c-000-001) vì:
1. Trong **alert subgraph** (đồ thị con chỉ chứa 7 service có alert), `recommender-svc` hoàn toàn cô lập (in_degree=0, out_degree=0). Lý do: service trung gian `catalog-svc` KHÔNG có alert → edge `catalog-svc → recommender-svc` không tồn tại trong alert subgraph.
2. Recommender-svc là **sink** (root cause candidate), nhưng không chia sẻ **ancestor chung** với bất kỳ sink nào khác (payment, cart, notification, search) trong alert subgraph → Union-Find giữ nó riêng biệt.

Thực tế, recommender-svc alert vì **batch retrain** (note: *"unrelated — concurrent batch retrain"*) — hoàn toàn độc lập với payment pool exhaustion.

**So sánh:** Phiên bản cũ (undirected graph) gộp recommender vào cluster chính vì `recommender ↔ edge-lb = 2 hops` (qua catalog-svc). Phiên bản mới loại bỏ false positive này nhờ chỉ xét topology trên các service đang thực sự alert.

### 8.5 Limitation lớn nhất của topology grouping

**Limitation ban đầu (đã khắc phục):** Union-Find với undirected graph tạo ra **single-linkage clustering** — chỉ cần 1 cặp service trong phạm vi max_hop là TOÀN BỘ nhóm bị merge.

**Khắc phục đã áp dụng:** Chuyển sang **Directed-Propagation** topology grouping:
1. Dựng **alert subgraph** — chỉ giữ node có alert → loại bỏ service trung gian không liên quan (catalog-svc).
2. Tìm **Sinks** (out-degree=0) — Root Cause candidates.
3. Gộp sink qua **shared non-edge ancestors** — chỉ gộp nếu chia sẻ caller chung thuộc tầng nghiệp vụ (không phải edge-lb).
4. Route caller/symptom vào sink group dựa trên **reachability** trong alert subgraph.

**Limitation còn lại:** Nếu 2 incident khác nhau cùng ảnh hưởng các service có call-chain chung (ví dụ: cả database crash và network outage đều ảnh hưởng checkout-svc), thuật toán vẫn có thể gom nhầm. Để giải quyết, cần kết hợp thêm **causal inference** (phân tích metric trend: service nào degrade trước?) hoặc **NLP trên alert note** để hiểu ngữ nghĩa sâu hơn.

---

## Kết quả Pipeline

| Metric | Value |
|---|---|
| Input alerts | 20 |
| Output clusters | 3 |
| Reduction ratio | 0.85 (85%) |
| Parameters | gap_sec=120, max_hop=2 |
| Algorithm | Directed-Propagation (sink-based) |

| Cluster | Services | Alerts | Severity | Ý nghĩa |
|---|---|---|---|---|
| c-000-000 | payment, checkout, edge-lb, cart, notification | 18 | crit | Payment pool exhaustion cascade |
| c-000-001 | recommender-svc | 1 | warn | ML batch retrain (independent) |
| c-000-002 | search-svc | 1 | warn | Independent slow query |

**Acceptance criteria:**
- ✅ Notebook chạy được, ≥ 3 cell có output
- ✅ results/cluster_summary.json tồn tại và valid JSON
- ✅ Cluster có services list và time_range
- ✅ reduction_ratio = 0.85 ≥ 0.5
- ✅ SUBMIT.md ≥ 100 từ, có design trade-off discussion
- ✅ 3 clusters — đúng mục tiêu 3-5 clusters của đề bài
