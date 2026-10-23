// 页面加载时初始化空图表
document.addEventListener('DOMContentLoaded', () => {
    drawBode([], [], []);
});

async function startSweep() {
    const btn = document.getElementById('start-btn');
    const isBufferOn = document.getElementById('buffer-toggle').checked;
    
    // 按钮进入“进行中”状态
    btn.disabled = true;
    btn.innerText = "扫频中...";
    document.getElementById('ai-topo').innerText = "采集中...";
    document.getElementById('ai-conf').innerText = "--%";

    try {
        // 向后端发起开始指令，并传递缓冲器开关状态
        const response = await fetch('/api/start_sweep', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_buffer: isBufferOn })
        });
        
        const resData = await response.json();
        
        if (resData.status === 'success') {
            // 1. 画图（严格对应 {"freqs": [], "mag_db": [], "phase_deg": []} 格式）
            drawBode(resData.data.freqs, resData.data.mag_db, resData.data.phase_deg);
            
            // 2. 处理 AI 结果
            if (resData.ai && resData.ai.label) {
                document.getElementById('ai-topo').innerText = resData.ai.label;
                document.getElementById('ai-conf').innerText = (resData.ai.confidence * 100).toFixed(1) + "%";
            } else {
                // 如果模型还没接入，显示待接入状态
                document.getElementById('ai-topo').innerText = "模型待接入";
                document.getElementById('ai-conf').innerText = "等待实测数据训练";
            }
        } else {
            alert("测量失败：" + resData.msg);
        }
    } catch (error) {
        console.error(error);
        alert("网络请求失败，请检查后端是否启动。");
    } finally {
        // 恢复按钮状态
        btn.disabled = false;
        btn.innerText = "开始扫频测量";
    }
}

// 核心画图函数
function drawBode(freqs, mag, phase) {
    // 1. 幅频图
    const magTrace = {
        x: freqs, 
        y: mag, 
        type: 'scatter', 
        mode: 'lines+markers',
        marker: {size: 3}, // 加入圆点表示 240 个离散实测点
        line: { color: '#1f77b4', width: 2 }
    };
    const magLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#eee' },
        yaxis: { title: '增益 (dB)', gridcolor: '#eee' },
        margin: { t: 10, b: 40, l: 50, r: 20 }, 
        height: 260,
        plot_bgcolor: 'transparent', 
        paper_bgcolor: 'transparent'
    };
    Plotly.react('mag-plot', [magTrace], magLayout);

    // 2. 相频图
    const phaseTrace = {
        x: freqs, 
        y: phase, 
        type: 'scatter', 
        mode: 'lines+markers',
        marker: {size: 3},
        line: { color: '#ff7f0e', width: 2 }
    };
    const phaseLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#eee' },
        yaxis: { title: '相位 (度)', gridcolor: '#eee' },
        margin: { t: 10, b: 40, l: 50, r: 20 }, 
        height: 260,
        plot_bgcolor: 'transparent', 
        paper_bgcolor: 'transparent'
    };
    Plotly.react('phase-plot', [phaseTrace], phaseLayout);
}