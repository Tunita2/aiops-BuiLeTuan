# FINDINGS — W2-D2: Root Cause Analysis

## 1. Cluster chính: c-000-000

### Root Cause: `payment-svc`

Cluster c-000-000 gồm 18 alert từ 5 service: payment-svc, checkout-svc, edge-lb, cart-svc, notification-svc. Đây là cluster lớn nhất và nghiêm trọng nhất (max severity = crit).

**Kết luận RCA:** `payment-svc` là root cause (culprit) với class = `connection_pool_exhaustion`. Các bằng chứng:

1. **Timestamp:** payment-svc là service phát alert sớm nhất trong cluster (09:42:01), sớm hơn checkout-svc 44 giây. Điều này cho thấy lỗi bắt nguồn từ payment-svc rồi lan truyền ngược chiều gọi (downstream → upstream).

2. **Topology (Sink):** Trong alert subgraph, payment-svc có out_degree = 0 (không gọi service nào khác đang alert). Đây là sink — terminal node trong chuỗi gọi dịch vụ. checkout-svc gọi payment-svc, edge-lb gọi checkout-svc → khi payment-svc hỏng, cả chuỗi phía trên đều bị ảnh hưởng.

3. **Metric pattern:** Alert đầu tiên của payment-svc là `db_connection_pool_used_ratio` tăng từ 0.85 (warn) lên 0.99 rồi 1.00 (crit), cho thấy connection pool bị chiếm hết. Tiếp theo latency_p99 nhảy lên 1840ms và error_rate tăng từ 4% lên 18%. Đây là pattern điển hình của connection pool exhaustion.

4. **Historical match:** Incident tương tự nhất là INC-2025-11-08 (similarity = 1.0): "Payment-svc v3.2 deploy at 09:42 leak DB pool. Pool 50/50 used trong 5 phút. Downstream checkout cascade." — trùng khít với tình huống hiện tại.

### Confidence: 0.77

Confidence 0.77 ở mức khá cao. Tuy nhiên, chưa đủ tự tin để triển khai auto-rollback tự động mà không cần SRE xác nhận. Lý do: có 2 incident lịch sử cùng đạt similarity = 1.0 (INC-2025-11-08 connection_pool_exhaustion và INC-2026-03-20 ddos) — mặc dù kNN chọn đúng connection_pool_exhaustion nhưng việc tồn tại 2 candidate cùng score cho thấy hệ thống retrieval cần thêm tín hiệu (metric pattern, fingerprint) để phân biệt chính xác hơn.

## 2. Cluster c-000-001: recommender-svc

Cluster chỉ có 1 alert (cpu_utilization warn) từ recommender-svc. Alert note ghi rõ "unrelated — concurrent batch retrain", cho thấy đây là sự kiện độc lập, không liên quan đến sự cố chính. Class được gán là `memory_leak` dựa trên kNN từ INC-2025-08-02, nhưng thực tế đây có thể chỉ là CPU spike do batch job. Confidence = 1.0 (single service) nhưng mức tin cậy thực tế thấp hơn vì chưa đủ context.

## 3. Cluster c-000-002: search-svc

Cluster chỉ có 1 alert (`catalog_db_query_time_ms` warn). Note ghi "noise — independent slow query". Đây là noise, không liên quan sự cố chính. Class `n_plus_1` từ kNN không chính xác vì thiếu thông tin. Tuy nhiên, pipeline xử lý đúng bằng cách tách riêng thành cluster độc lập.

## 4. Tại sao không chọn Bonus Path

Retrieval-only (keyword similarity) đã đủ cho dataset này vì:

1. **Dataset nhỏ (30 incidents):** Keyword matching cho kết quả chính xác, không cần TF-IDF hay embedding phức tạp.
2. **Pattern rõ ràng:** Cluster chính có services trùng khít với các incident lịch sử (payment-svc + checkout-svc + connection pool). Similarity score = 1.0 cho thấy match rất mạnh.
3. **Không cần LLM:** kNN top-1 đã trả đúng class (`connection_pool_exhaustion`) và actions phù hợp. LLM sẽ tốn thêm chi phí và latency mà không cải thiện đáng kể accuracy trên dataset này.

Nếu dataset mở rộng (hàng trăm incidents, pattern phức tạp hơn), TF-IDF hoặc sentence-transformer sẽ cần thiết để phân biệt các incident có keyword tương tự nhưng ngữ cảnh khác nhau.
