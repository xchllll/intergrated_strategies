from flask import Flask, render_template, jsonify, request
from mock_data import get_mock_sweep

app = Flask(__name__)

# 全局变量，用来存储“最新”的一次扫频数据。
# 初始状态用假数据填充，保证网页一打开有图看。
latest_sweep_data = get_mock_sweep()

@app.route('/')
def index():
    # 访问网页首页时，返回 HTML 页面
    return render_template('index.html')

@app.route('/api/sweep/latest', methods=['GET'])
def get_latest():
    # 网页前端调用这个接口来获取最新的数据画图
    return jsonify(latest_sweep_data)

@app.route('/api/sweep/push', methods=['POST'])
def push_sweep_data():
    """
    为硬件预留的接口：硬件跑完扫频后，通过 POST 请求把 JSON 发到这里。
    数据结构: {"freqs": [...], "mag_db": [...], "phase_deg": [...]}
    """
    global latest_sweep_data
    data = request.get_json()
    
    # 简单的数据校验，确保硬件传来的是对的
    if not data or 'freqs' not in data or 'mag_db' not in data or 'phase_deg' not in data:
        return jsonify({"status": "error", "msg": "数据格式错误"}), 400
        
    # 更新内存中的最新数据
    latest_sweep_data = data
    
    # （为第二部分 ML 铺垫）在这里，未来我们会调用 common/features.py 提取特征，
    # 然后调用 ml/infer.py 里的模型进行预测，把结果一起返回。
    
    return jsonify({"status": "success", "msg": "数据已接收，网页刷新即可看到最新波形"})

if __name__ == '__main__':
    # host="0.0.0.0" 允许局域网内的其他设备（比如你的 PYNQ 板子）访问这个服务
    app.run(host="0.0.0.0", port=5000, debug=True)