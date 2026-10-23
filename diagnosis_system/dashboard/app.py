from flask import Flask, render_template, jsonify, request
from mock_data import get_mock_sweep
import time
import sys
import os

# 把项目根目录加入环境变量，这样才能导入 ml 里的模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

app = Flask(__name__)

# 全局变量存储最近一次处理好的数据（等 T1 接入后会更新这里）
latest_clean_data = None
latest_ai_result = None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start_sweep', methods=['POST'])
def start_sweep():
    global latest_clean_data, latest_ai_result
    
    req_data = request.get_json()
    use_buffer = req_data.get('use_buffer', False)
    
    print(f"收到网页指令，开始扫频。缓冲器状态: {use_buffer}")
    
    # =========================================================
    # 【硬件与降噪接口区】（T1 负责实现）
    # 这里将来直接调用 T1 写好的函数（触发硬件 -> 采集 -> 查表校准 -> 降噪）
    # 返回值必须是标准的 dict: {"freqs": [...], "mag_db": [...], "phase_deg": [...]}
    # =========================================================
    
    # 目前用 sleep 模拟硬件耗时，用 mock 数据占位
    time.sleep(1.5) 
    clean_data = get_mock_sweep() 
    
    # 把干净数据存下来
    latest_clean_data = clean_data
    
    # =========================================================
    # 【AI 接口区】（你负责实现）
    # 拿到 clean_data 后，等 ML 模型训练好，在这里调用推理。
    # 预期返回: {"label": "类型", "confidence": 0.95}
    # =========================================================
    try:
        from ml.infer import predict_topology
        latest_ai_result = predict_topology(clean_data)
    except Exception as e:
        print(f"AI 推理失败: {e}")
        latest_ai_result = None 

    # 把结果打包回传给网页
    return jsonify({
        "status": "success",
        "data": {
            "freqs": clean_data["freqs"],       
            "mag_db": clean_data["mag_db"],     
            "phase_deg": clean_data["phase_deg"] 
        },
        "ai": latest_ai_result
    })

# 备用接口：如果 T1 的代码是完全独立运行的进程，可以通过这个接口推数据
@app.route('/api/sweep/push', methods=['POST'])
def push_sweep_data():
    global latest_clean_data
    data = request.get_json()
    
    if not data or 'freqs' not in data or 'mag_db' not in data or 'phase_deg' not in data:
        return jsonify({"status": "error", "msg": "数据格式错误，必须包含 freqs, mag_db, phase_deg"}), 400
        
    latest_clean_data = data
    print("成功接收到外部(PS侧)推送的干净扫频数据！")
    
    return jsonify({"status": "success", "msg": "数据已接收"})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)