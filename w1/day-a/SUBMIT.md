# W1-D1 Submission: Metric Anomaly Detection

## 1. EDA Reflection
- Data Type: Từ biểu đồ Skewness và Histogram, dữ liệu bị nghiêng (skewed) về bên trái (âm) hoặc có phân bố đều đặn quanh dải trung bình, nhưng có các giá trị giảm đột ngột (system failure).
- Phương pháp đã chọn: Dùng Z-Score cho Detector 1 (vì dễ áp dụng và hiệu quả bắt giá trị vượt ngưỡng trung bình) và Isolation Forest cho Detector 2 (để bắt anomaly nhiều features: mean, std, rate_of_change).

## 2. Model Tuning Logs
Thử nghiệm với tham số `contamination` của Isolation Forest:
1. Contamination = 0.01 -> Precision: ..., Recall: ..., F1: ...
2. Contamination = 0.02 -> Precision: ..., Recall: ..., F1: ...
3. Contamination = 0.05 -> Precision: ..., Recall: ..., F1: ...

## 3. Comparison
| Metric | Z-Score (3-Sigma) | Isolation Forest |
|---|---|---|
| Precision | (Điền số thực tế sau khi chạy code) | (Điền số thực tế sau khi chạy code) |
| Recall | (Điền số thực tế sau khi chạy code) | (Điền số thực tế sau khi chạy code) |
| F1 | (Điền số thực tế sau khi chạy code) | (Điền số thực tế sau khi chạy code) |
| False Alarms | (Điền số thực tế sau khi chạy code) | (Điền số thực tế sau khi chạy code) |

## 4. Final Reflection
- Model nào tốt hơn? Tùy thuộc vào việc Recall hay Precision quan trọng hơn, nhưng thường Isolation Forest có độ chính xác cao hơn sau khi thêm feature context.
- Trade-off giữa 2 phương pháp? Z-Score cực kỳ nhanh nhưng có thể miss hoặc báo nhầm nếu bị Skew nặng. Isolation Forest cần train lâu hơn, phức tạp hơn nhưng bắt được correlation.
- Production choice: Trong AIOps, hệ thống sẽ ưu tiên các Model có **Recall cao**, thà báo động nhầm (False Alarm) còn hơn bỏ sót (Miss) sự cố thực sự làm mất thời gian downtime và gây thiệt hại lớn (revenue lost). Dùng Z-Score làm First-pass và Isolation Forest làm Second-pass filter là lý tưởng nhất.
