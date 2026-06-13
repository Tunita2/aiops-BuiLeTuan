# Tóm tắt thảo luận - W2D3: Model Serving — Đưa Pipeline Lên Production

Tài liệu này tóm tắt toàn bộ nội dung buổi thảo luận về lý thuyết bài học W2D3, yêu cầu bài tập và các câu hỏi kiến trúc thực tế nhằm chuẩn bị ngữ cảnh cho các buổi lab tiếp theo.

---

## 1. Tóm tắt Bài học & Yêu cầu thực tế (W2D3)

### Lý thuyết chính:
- **Từ Notebook lên API Service**: Chuyển đổi pipeline 3 lớp (Alert Correlation $\rightarrow$ RCA $\rightarrow$ LLM Enrichment) thành một HTTP service có tính sẵn sàng cao trên Production bằng **FastAPI**.
- **Quản lý Service Graph**: Phân tích các nguồn sinh graph (Manual, Tracing, Service Mesh), cơ chế cập nhật graph định kỳ (Reload) để tránh graph bị stale (cũ/lỗi thời) và khả năng mở rộng (Scale).
- **Latency Budget**: Đo lường và tối ưu hóa thời gian phản hồi của API, tập trung tối ưu các tác vụ gọi LLM (chiếm >90% latency) bằng cache (TTLCache), gọi song song (Concurrent/Async) và thiết lập timeout.
- **Liveness & Readiness Prob**: Phân biệt `/healthz` (App còn sống không) và `/readyz` (App đã sẵn sàng nhận traffic chưa - đã load xong Graph/History).
- **Self-Monitoring & Logging**: Tích hợp metrics Prometheus (`/metrics`) và cấu hình log dạng JSON để thu thập bằng Loki/ELK.

### Yêu cầu bài tập (W2-D3):
- **Cấu trúc thư mục nộp**: Nhánh `main`, thư mục `aiops-<tên>/w2/d3/`.
- **Ba file bắt buộc**:
  1. `serve.py`: Chứa ứng dụng FastAPI, endpoint `/healthz`, `/readyz`, `POST /incident` (có validation bằng Pydantic và đo latency qua middleware).
  2. `DESIGN.md`: Tài liệu thiết kế hệ thống (tối thiểu 100 từ).
  3. `SUBMIT.md`: Giải đáp các câu hỏi EOD Checkpoint (về latency, failure handling khi LLM sập, concurrency bottleneck).

---

## 2. Chi tiết nội dung thảo luận Q&A (Kiến trúc & Kỹ thuật)

### Q1: Async, Sync, IO-bound là gì và tại sao FastAPI tối ưu hơn các framework khác?
- **Sync (Đồng bộ)**: Code chạy tuần tự, dòng sau phải đợi dòng trước hoàn thành. Nếu gặp tác vụ tốn thời gian (gọi API, đọc file), CPU sẽ bị block (ngồi chờ).
- **Async (Bất đồng bộ)**: Cho phép chuyển sang làm việc khác trong lúc chờ một tác vụ tốn thời gian hoàn thành (qua cơ chế Event Loop).
- **IO-bound**: Các tác vụ nghẽn do truyền nhận dữ liệu (gọi LLM API, truy vấn DB, đọc ghi ổ cứng) mà không phụ thuộc vào CPU.
- **Ưu thế của FastAPI**: Chạy trên chuẩn **ASGI**, hỗ trợ native `async`/`await`. Khi có hàng trăm request gọi LLM cùng lúc (tác vụ IO-bound cực nặng), FastAPI sử dụng Single-Thread Event Loop để gửi tất cả yêu cầu đi đồng thời mà không bị block luồng xử lý chính. Flask (Sync/WSGI) sẽ nhanh chóng bị cạn kiệt thread/process để xử lý.

### Q2: Pydantic là thư viện của Python hay nằm trong FastAPI?
- **Pydantic là một thư viện Python độc lập** chuyên về validate dữ liệu và ép kiểu (data coercion) dựa trên Type Hints của Python.
- **FastAPI sử dụng Pydantic làm nền tảng xương sống** để định nghĩa Request/Response Schema. Khi cài đặt FastAPI, Pydantic tự động được cài đặt đi kèm như một dependency bắt buộc.

### Q3: Cơ chế sinh tài liệu tự động (Swagger UI tại `/docs`) hoạt động ra sao?
- **Bước 1**: Khi khởi chạy, FastAPI quét toàn bộ code, đọc các route, query parameters, và các class Pydantic.
- **Bước 2**: Nó dịch toàn bộ cấu trúc API này thành một file JSON chuẩn hóa theo định dạng **OpenAPI Specification** (truy cập được tại `/openapi.json`).
- **Bước 3**: Thư viện **Swagger UI** (hoặc **ReDoc** tại `/redoc`) được tích hợp sẵn sẽ đọc file JSON này và render ra trang web tài liệu động tương tác trực quan cho phép người dùng test API ngay lập tức.

### Q4: Thực tế trên Production, Alert Batch được thu thập như thế nào?
- **Thực tế**: Alert đến từ rất nhiều nguồn (Prometheus metric alerts, CloudWatch logs, APM Datadog, Kubernetes events).
- **Kiến trúc gom**: Ứng dụng FastAPI **không tự đi quét** từng nguồn (để tránh tight coupling). Thay vào đó:
  1. *Cách 1 (Phổ biến)*: Các nguồn đẩy alert về một **Alert Router tập trung** (như Prometheus Alertmanager, PagerDuty). Bộ định tuyến này gom lại thành batch rồi bắn Webhook tới FastAPI.
  2. *Cách 2 (Hướng sự kiện)*: Các nguồn đẩy alert dưới dạng event vào một **Message Queue / Event Bus** (Kafka, AWS EventBridge, SQS). Service FastAPI sẽ subscribe và consume các event này từ hàng đợi.

### Q5: Thực tế nên deploy lên AWS EC2 hay dùng AWS Lambda là đủ?
- **AWS Lambda (Serverless)**: Phù hợp với quy mô nhỏ, alert ít phát sinh để tối ưu chi phí. Tuy nhiên, nó gặp hạn chế về **Cold Start** (mỗi lần khởi động lạnh phải tốn thời gian load lại Service Graph lớn lên bộ nhớ), không thể chạy **background thread** để tự động reload graph mỗi 5 phút, và khó tích hợp Prometheus `/metrics` thông thường.
- **EC2 / ECS / EKS (Containers)**: Là lựa chọn tiêu chuẩn của doanh nghiệp. Ứng dụng chạy liên tục 24/7 giúp giữ Service Graph trên RAM (load 1 lần lúc startup), dễ dàng chạy các tiến trình ngầm (cập nhật graph định kỳ) và tương thích hoàn hảo với Prometheus monitoring.

---
*Tài liệu này được lưu trữ để phục vụ cho việc phục hồi ngữ cảnh thảo luận ở các session làm việc tiếp theo.*
