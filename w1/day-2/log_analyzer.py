import sys
import pandas as pd
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

def analyze_log(log_file):
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    print(f"Total lines: {len(lines)}")
    
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.5
    miner = TemplateMiner(config=config)
    
    parsed = []
    for line in lines:
        parsed.append(miner.add_log_message(line.strip()))
        
    clusters = list(miner.drain.clusters)
    print(f"Unique templates: {len(clusters)}")
    
    clusters.sort(key=lambda x: x.size, reverse=True)
    print("\n--- Top 5 Templates ---")
    for i, c in enumerate(clusters[:5]):
        pct = (c.size / len(lines)) * 100
        print(f"{i+1}. [{c.cluster_id}] (count={c.size}, {pct:.2f}%): {c.get_template()}")

    data = []
    for i, r in enumerate(parsed):
        line = lines[i]
        parts = line.split()
        if len(parts) >= 2:
            try:
                # Naive parsing assuming HDFS format: 081109 203615
                if len(parts[0]) == 6 and len(parts[1]) == 6:
                    ts_str = f"20{parts[0]} {parts[1]}"
                    ts = pd.to_datetime(ts_str, format='%Y%m%d %H%M%S')
                    data.append({'timestamp': ts, 'template_id': r['cluster_id'], 'template': r['template_mined']})
            except:
                pass
    
    if not data:
        print("\nCould not parse timestamps or unsupported log format.")
        return
        
    df = pd.DataFrame(data)
    
    # "last hour" approximation for demo purposes
    max_time = df['timestamp'].max()
    one_hour_ago = max_time - pd.Timedelta(hours=1)
    
    recent_df = df[df['timestamp'] > one_hour_ago]
    old_df = df[df['timestamp'] <= one_hour_ago]
    
    recent_counts = recent_df['template_id'].value_counts()
    old_counts = old_df['template_id'].value_counts()
    
    if len(old_df) > 0:
        time_span_hours = (old_df['timestamp'].max() - old_df['timestamp'].min()).total_seconds() / 3600
        if time_span_hours == 0: time_span_hours = 1
    else:
        time_span_hours = 1
        
    print("\n--- Spiking Templates (Last Hour) ---")
    for tid, count in recent_counts.items():
        old_avg = (old_counts.get(tid, 0) / time_span_hours)
        if count > old_avg * 2 and count > 5:
            template_str = df[df['template_id'] == tid]['template'].iloc[0]
            print(f"Spike: Template {tid} (Recent count: {count}, Old Avg: {old_avg:.2f}) -> {template_str}")
            
    print("\n--- New Templates (Last Hour) ---")
    old_tids = set(old_df['template_id'].unique())
    recent_tids = set(recent_df['template_id'].unique())
    new_tids = recent_tids - old_tids
    if not new_tids:
        print("No new templates found.")
    for tid in new_tids:
        template_str = recent_df[recent_df['template_id'] == tid]['template'].iloc[0]
        print(f"New Template {tid}: {template_str}")
        
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python log_analyzer.py <logfile>")
        sys.exit(1)
    analyze_log(sys.argv[1])
