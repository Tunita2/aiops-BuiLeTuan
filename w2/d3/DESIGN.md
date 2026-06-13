# DESIGN.md — AIOps Incident Pipeline Serving Layer

## 1. Pipeline Architecture trong Endpoint

Endpoint `POST /incident` nhận batch alert thô từ hệ thống monitoring qua HTTP, chạy qua pipeline 2 lớp end-to-end:

```
Client POST /incident
    │
    ▼
[Pydantic Validation] ── sai format → 422
    │
    ▼
[Layer 1: Correlate (D1)]
    ├─ Dedup (fingerprint)
    ├─ Session Window (gap_sec=120s)
    ├─ Semantic Merge (Jaccard similarity)
    └─ Topology Grouping (directed-propagation, max_hop=2)
    │
    ▼
[Pick primary cluster] ── largest by alert_count
    │
    ▼
[Layer 2: RCA (D2)]
    ├─ Graph + Temporal Scoring (sink detection + PageRank + timestamp)
    ├─ Keyword Retrieval (similar incidents from history)
    ├─ kNN Classifier (class + actions từ top-1 similar)
    └─ Validation Guard (hallucination check)
    │
    ▼
[Serialize → IncidentResponse JSON] → 200 OK
```

Service graph (`services.json`) và incident history (`incidents_history.json`) được load **1 lần duy nhất** vào biến module-level (`GRAPH`, `HISTORY`) khi server khởi động. Các request sau chỉ đọc từ RAM — không tốn I/O.

## 2. Latency Budget Breakdown

Với dataset 20 alert mẫu, ước tính latency từng phase:

| Phase               | Estimated Latency | Tỷ lệ  | Scale behavior       |
|---------------------|-------------------|---------|----------------------|
| Pydantic validation | ~1ms              | ~1%     | Linear (N alerts)    |
| Correlate (D1)      | ~5-15ms           | ~10-15% | O(N²) worst-case     |
| RCA graph+temporal  | ~2-5ms            | ~5%     | O(V·E) — fixed cost  |
| Retrieve similar    | ~1-3ms            | ~3%     | Linear (history size)|
| Classify + validate | ~0.5ms            | ~1%     | Fixed cost           |
| JSON serialize      | ~1ms              | ~1%     | Linear (output size) |

**Tổng p99 ước tính: ~15-25ms** (không có LLM call).

Nếu có LLM call, phase LLM sẽ chiếm **~91% tổng latency** (~2-8 giây), đẩy tổng p99 lên ~3-10 giây. Đây là bottleneck rõ ràng nhất. Các kỹ thuật tối ưu: cache prompt (TTLCache), async concurrent calls, dùng model nhỏ (gpt-4o-mini), hoặc skip LLM khi graph confidence ≥ 0.9.

## 3. Production Concern: Fault Tolerance khi LLM Provider Down

**Vấn đề**: Nếu LLM provider (OpenAI/Anthropic) bị outage giữa lúc chạy, endpoint sẽ hang do timeout LLM call → connection pool cạn kiệt → toàn bộ service bị stuck.

**Giải pháp đã implement**:

1. **Feature Flag `AIOPS_USE_LLM`**: Environment variable cho phép tắt LLM call ngay lập tức bằng cách set `AIOPS_USE_LLM=false` + restart pod. Endpoint vẫn hoạt động với graph-only RCA quality, không cần redeploy code.

2. **Timeout bắt buộc**: Mọi outbound call phải có timeout (10s cho LLM, 3s connect). Không timeout = failure mode phổ biến nhất.

3. **Graceful degradation**: Pipeline hiện tại dùng `graph+knn` method (không gọi LLM), là fallback path tự nhiên. Khi LLM available, có thể nâng cấp thêm enrichment layer mà không thay đổi pipeline core.

4. **Stateless design**: Mỗi request độc lập, không có shared mutable state giữa các request. Single-worker chấp nhận limitation về throughput — document rõ ràng thay vì implement phức tạp không cần thiết.

## 4. Trade-off: Tại sao chọn FastAPI thay vì Flask/BentoML

| Tiêu chí            | FastAPI ✅         | Flask              | BentoML            |
|----------------------|--------------------|--------------------|---------------------|
| Async support        | Native (ASGI)     | Không (WSGI)       | Có nhưng abstracted |
| Input validation     | Pydantic tự động  | Phải tự viết       | Có nhưng khác API   |
| API documentation    | OpenAPI tự sinh    | Phải cài extension | Không focus web API  |
| Learning curve       | Thấp-trung bình   | Rất thấp           | Cao                 |
| Pipeline flexibility | Cao — bring your own | Cao              | Thấp — model-centric|

**Lý do chọn FastAPI**:

- Pipeline AIOps có LLM call (IO-bound) → cần async native để xử lý concurrent requests hiệu quả mà không tốn nhiều thread/process.
- Input schema phức tạp (Alert có 8 field, nested list) → Pydantic validation tự động giúp loại bỏ hoàn toàn code validation thủ công, đảm bảo không bao giờ trả 500 khi input sai.
- OpenAPI auto-documentation (`/docs`) cho phép SRE test endpoint trực tiếp từ browser mà không cần Postman hay curl phức tạp.
- BentoML bị loại vì pipeline này không phải ML model-centric — nó là mixed workload (graph analysis + keyword retrieval + optional LLM). BentoML overhead cao cho non-ML workload.
- Flask bị loại vì thiếu async support — khi gọi LLM (5-8s mỗi call), Flask sync sẽ block toàn bộ worker trong thời gian chờ.

Chọn `gap_sec=120s` vì đây là sweet spot cho hầu hết production system: đủ ngắn để tách 2 sự cố riêng biệt, đủ dài để gom cascade alert trong cùng 1 incident (alert thường fire liên tiếp trong vòng 30-90s).
