import pandas as pd
import sys
import matplotlib.pyplot as plt

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def estimate_cost(tier_name, services, log_gb_per_day, metric_events_per_sec, multiplier=1):
    # Apply sensitivity multiplier
    log_gb_per_day = log_gb_per_day * multiplier
    metric_events_per_sec = metric_events_per_sec * multiplier
    
    # 1. BUILD COST (Self-hosted)
    
    # Tiering Strategy cho Logs: 7 days hot, 23 days warm, 335 days cold
    # Hot: ~$2.5/GB/month (SSD, Elasticsearch)
    # Warm: ~$0.5/GB/month (HDD)
    # Cold: ~$0.023/GB/month (S3)
    hot_logs = log_gb_per_day * 7 * 2.5
    warm_logs = log_gb_per_day * 23 * 0.5
    cold_logs = log_gb_per_day * 335 * 0.023 / 12  # monthly amortized
    log_storage_build = hot_logs + warm_logs + cold_logs
    
    # Storage Metrics (VictoriaMetrics)
    metric_cost_build = (metric_events_per_sec / 1000) * 2
    
    # Compute Cost (Kafka, Flink) - computed based on throughput
    # Baseline $500 for small, scaling linearly with events & logs
    compute_cost_build = 500 + (log_gb_per_day * 1.5) + (metric_events_per_sec / 1000 * 1.5)
    
    # Egress Cost (Cross-AZ/Region transfer) - Assume 20% of logs cross AZ at $0.01/GB
    egress_cost_build = log_gb_per_day * 30 * 0.2 * 0.01
    
    total_build = log_storage_build + metric_cost_build + compute_cost_build + egress_cost_build
    
    # 2. BUY COST (SaaS - Datadog)
    
    # Datadog Log Ingest & Retain: $0.10 ingest/GB + $1.27 per M events indexed
    # Assume 1 GB ~ 1,000,000 events
    log_cost_buy = log_gb_per_day * 30 * (0.10 + 1.27)
    
    # Datadog Metrics: Pro tier ~$15 per host + custom metrics
    # Assume 1 host produces 1000 events/sec. 
    metric_cost_buy = (metric_events_per_sec / 1000) * 15 + (metric_events_per_sec / 100) * 5
    
    total_buy = log_cost_buy + metric_cost_buy
    
    return {
        'Scenario': f"{tier_name} ({multiplier}x)",
        'Logs (GB/day)': log_gb_per_day,
        'Metrics (eps)': metric_events_per_sec,
        'Build Cost': round(total_build, 2),
        'Buy Cost': round(total_buy, 2)
    }

def plot_cost_comparison(df):
    plt.figure(figsize=(12, 6))
    x = range(len(df))
    width = 0.35
    
    plt.bar([i - width/2 for i in x], df['Build Cost'], width, label='Build Cost', color='skyblue')
    plt.bar([i + width/2 for i in x], df['Buy Cost'], width, label='Buy Cost', color='salmon')
    
    plt.title('Cost Comparison: Build vs Buy Across Tiers & Volumes')
    plt.xlabel('Scenarios')
    plt.ylabel('Monthly Cost (USD)')
    plt.xticks(x, df['Scenario'], rotation=45, ha='right')
    plt.legend()
    plt.tight_layout()
    plt.savefig('cost_comparison.png')
    print("Đã lưu biểu đồ cost_comparison.png")

def main():
    base_tiers = [
        ('Small', 10, 50, 100000),
        ('Medium', 100, 500, 1000000),
        ('Large', 1000, 5000, 10000000)
    ]
    
    multipliers = [0.5, 1, 2, 5]
    
    results = []
    # Test sensitivity cho Medium tier
    for t in base_tiers:
        if t[0] == 'Medium':
            for m in multipliers:
                results.append(estimate_cost(t[0], t[1], t[2], t[3], multiplier=m))
        else:
            results.append(estimate_cost(t[0], t[1], t[2], t[3]))
            
    df = pd.DataFrame(results)
    
    print("\n--- COST ESTIMATION & SENSITIVITY ANALYSIS ---")
    print(df.to_markdown(index=False))
    
    # In ra breaking point analysis
    print("\n[Breaking Point Analysis]")
    for _, row in df.iterrows():
        diff = row['Buy Cost'] - row['Build Cost']
        if diff > 0:
            ratio = row['Buy Cost'] / row['Build Cost']
            print(f"Tại {row['Scenario']}: Build rẻ hơn Buy {ratio:.1f} lần (Tiết kiệm ${diff:,.0f}/tháng)")
        else:
            print(f"Tại {row['Scenario']}: Buy rẻ hơn Build (Tiết kiệm ${-diff:,.0f}/tháng)")

    # Plot
    plot_cost_comparison(df)

if __name__ == "__main__":
    main()
