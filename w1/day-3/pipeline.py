import pandas as pd
import queue
import threading
import time
import sys
import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Configuration
DATA_URL = "https://raw.githubusercontent.com/numenta/NAB/master/data/realKnownCause/machine_temperature_system_failure.csv"
OUTPUT_FILE = "features.parquet"
WINDOW_SIZE = 12 # Since granularity is 5-min, 12 means 1 hour

def producer(q: queue.Queue):
    print("Producer: Bắt đầu đọc dữ liệu...")
    try:
        df = pd.read_csv(DATA_URL)
        # Parse timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        total_rows = len(df)
        print(f"Producer: Đã tải {total_rows} dòng dữ liệu từ NAB.")
        
        for idx, row in df.iterrows():
            q.put({'timestamp': row['timestamp'], 'value': row['value']})
            # Progress indicator
            if (idx + 1) % 5000 == 0:
                print(f"Producer: Đã đẩy {idx + 1}/{total_rows} dòng...")
                
        print("Producer: Đã đẩy hết dữ liệu vào queue.")
    except Exception as e:
        print(f"Producer error: {e}")
    finally:
        q.put(None)

def consumer(q: queue.Queue):
    print("Consumer: Bắt đầu xử lý luồng dữ liệu...")
    features_list = []
    window = []
    processed_count = 0
    
    while True:
        item = q.get()
        if item is None:
            break
            
        value = item['value']
        window.append(value)
        if len(window) > WINDOW_SIZE:
            window.pop(0)
            
        # Extract features
        if len(window) == WINDOW_SIZE:
            rolling_mean = sum(window) / WINDOW_SIZE
            rolling_std = pd.Series(window).std()
            # Calculate slope over the whole window instead of just diff
            # slope = (y2 - y1) / (x2 - x1)
            rate_of_change = (window[-1] - window[0]) / (WINDOW_SIZE - 1)
        else:
            rolling_mean = None
            rolling_std = None
            rate_of_change = None
            
        features_list.append({
            'timestamp': item['timestamp'],
            'value': value,
            'rolling_mean_1h': rolling_mean,
            'rolling_std_1h': rolling_std,
            'rate_of_change': rate_of_change
        })
        
        processed_count += 1
        if processed_count % 5000 == 0:
            print(f"Consumer: Đã xử lý {processed_count} dòng...")
        
    print("Consumer: Đã xử lý xong dữ liệu. Đang lưu ra file...")
    out_df = pd.DataFrame(features_list)
    try:
        out_df.to_parquet(OUTPUT_FILE, engine='pyarrow')
        print(f"Consumer: Đã lưu kết quả tại {OUTPUT_FILE}")
    except Exception as e:
        print(f"Không thể lưu parquet: {e}. Thử lưu JSON...")
        # Convert datetime to string for JSON serialization if needed
        out_df['timestamp'] = out_df['timestamp'].astype(str)
        out_df.to_json("features.json", orient='records')
        print("Consumer: Đã lưu kết quả tại features.json")

if __name__ == "__main__":
    q = queue.Queue(maxsize=5000)
    
    prod_thread = threading.Thread(target=producer, args=(q,))
    cons_thread = threading.Thread(target=consumer, args=(q,))
    
    start_time = time.time()
    
    prod_thread.start()
    cons_thread.start()
    
    prod_thread.join()
    cons_thread.join()
    
    print(f"Hoàn thành trong {time.time() - start_time:.2f} giây.")
