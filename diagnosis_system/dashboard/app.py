from flask import Flask, render_template, jsonify, request
import time
import sys
import os
import csv
import math

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

app = Flask(__name__)

# =========================================================
# 【调试模式】读取本地的 CSV 数据（代替假数据）
# 请把那份 CSV 文件重命名为 data_rc_lpf.csv 并放在 dashboard 文件夹下
# =========================================================
CSV_FILE_PATH = os.path.join(os.path.dirname(__file__), "data_rc_lpf.csv")

def read_csv_sweep():
    freqs = []
    mags = []
    phases = []
    
    if os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                freqs.append(float(row["freq"]))
                mags.append(float(row["mag"]))
                phases.append(float(row["phase"]))
    else:
        print(f"警告：找不到 CSV 文件 {CSV_FILE_PATH}，将返回空数据。")
        
    return {
        "freqs": freqs,
        "mag_db": mags,
        "phase_deg": phases
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start_sweep', methods=['POST'])
def start_sweep():
    req_data = request.get_json()
    use_buffer = req_data.get('use_buffer', False)
    
    print(f"\n==== 收到网页扫频指令！缓冲器状态: {use_buffer} ====")
    
    # 1. 模拟硬件采集耗时
    time.sleep(1) 
    
    # 2. 读取数据（目前用本地 CSV 文件，以后换成 T1 硬件调用）
    clean_data = read_csv_sweep()
    
    # 3. 调用 AI 模型
    ai_result = None
    try:
        from ml.infer import predict_topology
        ai_result = predict_topology(clean_data)
        print(f"==== AI 识别成功：{ai_result['label']}，置信度：{ai_result['confidence']:.2f} ====")
    except Exception as e:
        print(f"!!!! AI 推理报错：{e}")
        
    return jsonify({
        "status": "success",
        "data": clean_data,
        "ai": ai_result
    })

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)