# SUBMIT.md — W2-D3 EOD Checkpoint

## Câu 1: Latency thực của endpoint ra sao?

*Đã chạy 20 request liên tiếp sử dụng 20 alerts thực tế từ file dataset alerts_sample.jsonl và đo qua header X-Response-Time-Ms:*

### Kết quả đo:

| Metric | Giá trị |
|--------|---------|
| p50    | 4.35 ms |
| p99    | 95.39 ms (bao gồm cả cold-start khởi tạo ở request đầu tiên, các request sau trung bình ~4.1 ms) |

### Phase breakdown (ước tính từ structured log):

| Phase              | Latency   | Scale behavior khi input ×10        |
|--------------------|-----------|--------------------------------------|
| Pydantic validate  | ~1ms      | Linear — validate N alert objects    |
| Correlate (D1)     | ~2.5ms    | O(N²) worst-case (pairwise compare) |
| RCA graph+temporal | ~0.5ms    | Fixed cost — chỉ chạy trên primary  |
| Retrieve similar   | ~0.8ms    | Linear — scan history list           |
| kNN classify       | ~0.1ms    | Fixed cost — chỉ lấy top-1          |
| JSON serialize     | ~0.1ms    | Linear — output size                 |

**Phase chiếm phần lớn**: Correlate (D1) — do phải chạy qua 4 layer (dedup → session → semantic → topology). Với 200 alert, pairwise semantic comparison sẽ tăng đáng kể.

**Fixed cost vs Linear**: RCA graph+temporal và kNN classify là fixed cost (chỉ chạy trên primary cluster, không phụ thuộc input size). Correlate và validate scale linear hoặc quadratic theo số alert đầu vào.

---

## Câu 2: LLM provider down hoặc 4 request đồng thời — endpoint handle ra sao?

*Đã test concurrency bằng Python ThreadPoolExecutor với 4 workers chạy tổng cộng 20 requests.*

### LLM Provider Down:

Pipeline hiện tại dùng method `graph+knn` — **không gọi LLM**. Do đó, khi LLM provider down, endpoint **không bị ảnh hưởng**.

Nếu tích hợp LLM enrichment trong tương lai:
- **Feature flag `AIOPS_USE_LLM=false`**: Tắt LLM call ngay lập tức, restart pod, endpoint chạy lại với graph-only quality trong 30 giây. Không cần redeploy code.
- **Timeout 10s bắt buộc**: LLM call hang sẽ bị kill sau 10s, trả fallback result thay vì block forever.
- Nếu không có fallback → 1 LLM call hang → endpoint hang → connection pool cạn → toàn bộ service stuck (cascading failure).

### 4 Request Đồng Thời:

**Kết quả test**:
- **Success Rate**: 100% (20/20 requests thành công với status 200).
- **Latency response time (từ server header)**: tăng lên từ ~13ms đến ~94.4ms khi 4 request bắn đồng thời.
- **Client roundtrip time (bao gồm network handshake)**: tăng lên ~2.4s cho đợt request đồng thời đầu tiên do chi phí khởi tạo kết nối HTTPX đồng thời trong các thread.

**Bottleneck đầu tiên quan sát được**: Với `--workers 1` (single process) và định nghĩa endpoint đồng bộ (`def post_incident`), FastAPI chạy các request này trên thread pool. Tuy nhiên, Python có GIL (Global Interpreter Lock), nên phần tính toán CPU-bound (correlate topology + RCA graph) thực tế vẫn bị tuần tự hóa trên một core duy nhất. GIL contention là bottleneck lớn nhất khiến latency tăng lên rõ rệt khi có nhiều requests tính toán đồng thời.

**Giải pháp**:
- Scale `--workers 4` để xử lý song song trên nhiều process thực thụ nhằm bypass GIL (trade-off: mỗi worker copy GRAPH + HISTORY chiếm thêm RAM).
- Chuyển hẳn pipeline nặng sang một task queue (như Celery/RQ) hoặc dùng Ray Serve để compose và scale các compute-heavy step.

---

## Câu 3: /healthz và /readyz check gì? Vì sao tách 2 endpoint?

### /healthz check gì:
- Chỉ return `{"status": "ok"}` — xác nhận process còn sống (liveness).
- Không check dependency nào. Nếu process có thể trả HTTP response = nó còn sống.

### /readyz check gì:
- `graph`: `GRAPH.number_of_nodes() > 0` — service graph đã load xong vào RAM.
- `history`: `len(HISTORY) > 0` — incident history đã load xong.
- **Không check LLM API** — readiness không nên depend external service quá chặt.

### Vì sao tách 2 endpoint:
Kubernetes (hoặc load balancer) sử dụng 2 probe cho 2 mục đích khác nhau:

| Probe     | Endpoint  | Khi fail thì sao?                          |
|-----------|-----------|----------------------------------------------|
| Liveness  | /healthz  | K8s **kill pod** và tạo pod mới (restart)    |
| Readiness | /readyz   | K8s **remove pod khỏi Service** (ngừng route traffic), nhưng KHÔNG kill |

Nếu gộp chung → khi graph chưa load xong (readiness fail), K8s sẽ kill pod liên tục (vì tưởng process chết) thay vì chờ nó load xong. Tách 2 endpoint giúp phân biệt "process bị crash" vs "process đang khởi động".

### Khi LLM API down, /readyz fail hay pass?
**Pass** — vì `/readyz` của tôi **không check LLM API**. Lý do: nếu readyz depend LLM, khi OpenAI down → tất cả pod bị mark not-ready → toàn bộ service offline. Trong khi pipeline vẫn có thể chạy với graph-only fallback. Đây là design decision có chủ đích: **readiness chỉ check internal state, không check external dependency**.
