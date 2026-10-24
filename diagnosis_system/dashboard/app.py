from flask import Flask, render_template, jsonify, request
import time
import sys
import os
import math
import csv
import platform # 新增：用于判断操作系统

# =========================================================
# 核心路径配置：把仓库里的所有相关文件夹都加进系统路径
# =========================================================
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

sys.path.insert(0, os.path.join(ROOT_DIR, 'board'))         # ps_ctrl.py
sys.path.insert(0, os.path.join(ROOT_DIR, 'python_model'))  # calibration.py
sys.path.insert(0, os.path.join(ROOT_DIR, 'data'))          # gen_dataset.py

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start_sweep', methods=['POST'])
def start_sweep():
    req_data = request.get_json()
    use_buffer = req_data.get('use_buffer', False)
    
    print(f"\n==== 收到网页扫频指令！缓冲器状态: {use_buffer} ====")
    
    clean_data = {"freqs": [], "mag_db": [], "phase_deg": []}
    
    # =========================================================
    # 核心判断：Windows 读 CSV 演示，Linux(板子) 跑真实硬件
    # =========================================================
    if platform.system() == 'Windows':
        print("==== 检测到 Windows 系统，自动切换为 CSV 演示模式 ====")
        time.sleep(1) 
        csv_path = os.path.join(ROOT_DIR, "data", "dataset_rc_lpf.csv")
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    clean_data["freqs"].append(float(row["freq"]))
                    clean_data["mag_db"].append(float(row["mag"]))
                    clean_data["phase_deg"].append(float(row["phase"]))
        else:
            print(f"!!!! 找不到 CSV: {csv_path}")
            return jsonify({"status": "error", "msg": "找不到 CSV 文件"}), 500
            
    else:
        print("==== 检测到 PYNQ 环境，开始真实硬件扫频 ====")
        try:
            import ps_ctrl
            import calibration as cal
            from gen_dataset import log_spaced_freqs
            
            cal_path = cal.CAL_PATH_BUFFER if use_buffer else cal.CAL_PATH_DIRECT
            freqs = log_spaced_freqs(20.0, 20000.0, 240)
            
            for f in freqs:
                acc_i, acc_q, window_n = ps_ctrl.measure_once(f, periods=10)
                mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
                mag_cal, phase_cal = cal.apply_calibration(mag, phase, f, path=cal_path)
                
                mag_db = 20.0 * math.log10(mag_cal) if mag_cal > 0 else -99.0
                
                clean_data["freqs"].append(f)
                clean_data["mag_db"].append(mag_db)
                clean_data["phase_deg"].append(phase_cal)
            print("==== 真实硬件扫频完成！ ====")
        except Exception as e:
            print(f"!!!! 扫频过程报错：{e}")
            return jsonify({"status": "error", "msg": str(e)}), 500

    # =========================================================
    # AI 识别（Windows 和板子都会走这里）
    # =========================================================
    ai_result = None
    try:
        from ml.infer import predict_topology
        ai_result = predict_topology(clean_data)
        print(f"==== AI 识别成功：{ai_result['label']} ====")
    except Exception as e:
        print(f"!!!! AI 推理报错：{e}")

    return jsonify({
        "status": "success",
        "data": clean_data,
        "ai": ai_result
    })

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)