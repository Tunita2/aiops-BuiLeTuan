# W3-D3 Submission — Bui Le Tuan

## Outage chosen
- ID: 3
- Name: Cloudflare WAF Regex 2019-07-02
- Why this one: I am interested in catastrophic backtracking (ReDoS) failure patterns because regular expressions are widely used in web application firewall (WAF) rule sets and input validation middlewares. Investigating how a single CPU-pinned process can degrade container response times and trigger cascading edge timeouts provides deep insight into designing resilient ingress systems.
- Failure mode: Catastrophic backtracking / CPU exhaustion

## 3 thứ tôi học từ outage này
1. **Quy luật backtracking của Regex:** Các ký tự lặp lồng nhau hoặc không được neo chặt (un-anchored) có thể dẫn tới sự bùng nổ số lượng nhánh duyệt (exponential runtime complexity) khi gặp chuỗi không khớp, làm đơ luồng xử lý của uvicorn/node.
2. **Ảnh hưởng Cascading do cạn kiệt tài nguyên CPU:** Việc CPU bị ghim 100% trên gateway làm tăng đột biến độ trễ của toàn bộ hệ thống phía sau, khiến client nhận phản hồi timeout (504 Gateway Timeout), gây khó khăn cho việc định vị lỗi nếu chỉ dựa vào HTTP status code.
3. **Sự phụ thuộc của hệ thống Giám sát:** Cần phải tách biệt luồng thu thập số liệu (monitoring) và luồng phục vụ người dùng. Nếu monitoring chạy chung tài nguyên CPU hoặc phụ thuộc vào chính dịch vụ đang lỗi để tự khám phá (service discovery), ta sẽ bị mù thông tin khi sự cố xảy ra (như trường hợp Roblox 2021).

## 1 thứ pipeline của tôi sẽ vẫn miss nếu outage này xảy ra real
- **Pattern:** Lỗi cạn kiệt tài nguyên (CPU/Memory exhaustion) do lỗi logic ứng dụng (ReDoS hoặc vòng lặp vô hạn).
- **Why miss:** Do pipeline hiện tại chỉ giám sát các chỉ số HTTP của các service cố định mà không thu thập tài nguyên container (CPU/RAM từ cAdvisor/node-exporter), đồng thời thiếu cơ chế tự động cấu hình target mới cho Prometheus (Service Discovery).
- **Mitigation idea:** Triển khai cAdvisor để lấy thông tin CPU container và áp dụng Prometheus `file_sd` để tự động cập nhật cấu hình scrape khi có container mới được dựng lên.

## 1 quyết định trong ADR mà tôi không hoàn toàn chắc
- Việc lựa chọn Prometheus file-based service discovery (`file_sd`) thông qua thư mục chia sẻ (shared volume) thay vì Consul hay HTTP service discovery (`http_sd`). 
- Mặc dù `file_sd` cực kỳ đơn giản và không có nguy cơ loop dependency như Consul, nhưng việc đọc/ghi đồng thời vào các file target JSON trong môi trường phân tán lớn có thể gặp tình trạng lock file hoặc độ trễ I/O đĩa.

## Cost model verdict cho stack của tôi
- **ROI:** 3.2 (Scenario 2 - 100 services) | 5.0 (Scenario 3 - 500 services)
- **Payback:** 0.31 tháng (Scenario 2) | 0.20 tháng (Scenario 3)
- **Verdict:** `worth_it` cho các hệ thống quy mô vừa và lớn có chi phí downtime cao.
