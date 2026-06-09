# SUBMIT — W2-D2: EOD Checkpoint

## Câu 1: Confidence của top-1 trong cluster lớn nhất

**Confidence top-1 (payment-svc trong cluster c-000-000): 0.77**

Nếu phải set threshold để auto-rollback mà không cần SRE xác nhận, tôi chọn **threshold = 0.90**.

**Lý do:**

- Ở mức 0.77, pipeline đã xác định đúng root cause và class, nhưng vẫn có rủi ro: hai incident lịch sử khác nhau (connection_pool_exhaustion và ddos) cùng đạt similarity = 1.0. Nếu pipeline chọn sai class (ddos thay vì connection_pool_exhaustion), action đề xuất sẽ sai hoàn toàn (WAF rate-limit thay vì rollback + tăng pool size).

- Auto-rollback là hành động có tác động lớn (tắt version mới, quay về version cũ). Nếu rollback sai service hoặc sai thời điểm, có thể gây downtime thêm. Vì vậy cần confidence rất cao (≥ 0.90) để đảm bảo an toàn.

- Ở threshold 0.90, chỉ các cluster single-service (confidence = 1.0) hoặc cluster có pattern cực kỳ rõ ràng mới được auto-rollback. Các cluster phức tạp hơn sẽ vẫn cần SRE xác nhận — đây là trade-off hợp lý giữa tốc độ phản hồi và an toàn.

- Trong production thực tế, tôi sẽ bắt đầu với threshold cao (0.95), theo dõi accuracy qua 50-100 incident, rồi dần hạ xuống 0.90 khi đã có đủ confidence vào pipeline.

## Câu 2: Variant classifier

**Variant đã chọn: A — Rule-based (keyword retrieval + kNN top-1)**

**Chạy thực tế ra sao:**

- Pipeline tính similarity score dựa trên 3 tín hiệu: root_cause_service match (+0.4), service overlap (+0.2/service, max +0.4), severity match (+0.2). Đơn giản, deterministic, không cần API key.

- Kết quả chính xác cho cluster c-000-000: kNN top-1 chọn đúng INC-2025-11-08 (connection_pool_exhaustion) với score = 1.0. Actions đề xuất phù hợp: Rollback + Tăng pool size + Thêm monitor.

- Hạn chế ở cluster nhỏ (c-000-001, c-000-002): kNN gán class dựa trên service name match, không phân tích sâu metric pattern. Ví dụ recommender-svc bị gán memory_leak nhưng thực tế chỉ là CPU spike do batch retrain.

**Trade-off với variant không chọn:**

- **Variant B (Free LLM — Groq):** LLM sẽ phân tích sâu hơn: đọc hiểu summary của incident lịch sử, kết hợp metric pattern (db_connection_pool_used_ratio tăng) để reasoning chính xác hơn. Nhưng thêm latency (2-5s/call), dependency vào dịch vụ bên ngoài, và risk hallucination (LLM có thể đoán bừa service không tồn tại). Với dataset 30 incidents và pattern rõ ràng, keyword retrieval đã đủ — LLM không tăng accuracy đáng kể.

- **Variant C (Paid LLM — GPT-4o):** Tương tự variant B nhưng output structured hơn (JSON schema enforcement). Chi phí ~$0.002/incident — rẻ, nhưng không justify khi rule-based đã đạt accuracy cao trên dataset hiện tại.

## Câu 3: Industry landscape

**Pipeline gần product nào nhất: Dynatrace Davis**

Cả hai cùng triết lý: **service graph (topology) là source of truth** để tìm root cause. Pipeline của tôi dùng service graph từ services.json + PageRank reverse + timestamp scoring — giống Davis dùng Smartscape service map + causal AI.

**Trong domain GeekShop, lựa chọn này hợp lý vì:**

1. **Service map ổn định:** GeekShop là hệ thống e-commerce với 10 services + 4 stores, topology ít thay đổi (không phải serverless hay event-driven phức tạp). Service graph từ services.json phản ánh đúng dependency thực tế.

2. **Alert volume vừa phải:** 20 alerts/incident, 3 clusters — đủ nhỏ để graph traversal xử lý trong <1 giây. Không cần ML/DL phức tạp như Causely.

3. **Deterministic:** Cùng input luôn cho cùng output — quan trọng cho audit và postmortem. LLM-based approach (Datadog Watchdog) có thể cho kết quả khác nhau mỗi lần chạy.

**Khi nào nên đổi:**

- Nếu GeekShop mở rộng sang microservices dynamic (auto-scaling, service mesh, serverless functions), service graph sẽ không ổn định → cần chuyển sang Causely (causal AI học từ data, không depend topology).

- Nếu alert volume tăng lên hàng nghìn/ngày, cần BigPanda/Moogsoft để dedup + noise reduction ở quy mô lớn trước khi RCA.
