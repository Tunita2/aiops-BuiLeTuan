import pandas as pd
import matplotlib.pyplot as plt
import sys
import matplotlib.dates as mdates

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    try:
        df = pd.read_parquet('features.parquet')
    except Exception as e:
        print(f"Lỗi đọc file parquet: {e}")
        return

    # Convert timestamp to datetime if it's string (although pipeline.py already outputs timestamp type if using parquet)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    plt.figure(figsize=(15, 8))
    plt.plot(df['timestamp'], df['value'], label='Original Value', alpha=0.4, color='blue', linewidth=1)
    
    # Plot mean
    plt.plot(df['timestamp'], df['rolling_mean_1h'], label='Rolling Mean (1h)', color='red', linewidth=2)
    
    # Fill standard deviation area
    plt.fill_between(df['timestamp'], 
                     df['rolling_mean_1h'] - df['rolling_std_1h'],
                     df['rolling_mean_1h'] + df['rolling_std_1h'],
                     color='red', alpha=0.2, label='±1 Std Dev')
                     
    plt.title('Machine Temperature: Original vs Rolling Features (1h Window)')
    plt.xlabel('Time')
    plt.ylabel('Temperature')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Format x-axis
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.gcf().autofmt_xdate() # Rotation
    
    plt.tight_layout()
    plt.savefig('features_plot.png', dpi=300)
    print("Đã lưu biểu đồ features_plot.png")

if __name__ == "__main__":
    main()
