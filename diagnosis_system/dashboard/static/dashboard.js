async function fetchData() {
    try {
        // 向后端请求最新数据
        const response = await fetch('/api/sweep/latest');
        const data = await response.json();
        // 拿到数据后，调用画图函数
        drawBode(data.freqs, data.mag_db, data.phase_deg);
    } catch (error) {
        console.error("获取数据失败:", error);
        alert("获取数据失败，请检查后端是否启动！");
    }
}

function drawBode(freqs, mag, phase) {
    // 1. 画幅频图 (Magnitude)
    const magTrace = {
        x: freqs,
        y: mag,
        type: 'scatter',
        mode: 'lines',
        name: '增益',
        line: { color: '#1f77b4', width: 2 }
    };
    const magLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)' }, // 注意：x轴是对数坐标
        yaxis: { title: '增益 (dB)' },
        margin: { t: 20, b: 40, l: 50, r: 20 },
        height: 300
    };
    Plotly.react('mag-plot', [magTrace], magLayout);

    // 2. 画相频图 (Phase)
    const phaseTrace = {
        x: freqs,
        y: phase,
        type: 'scatter',
        mode: 'lines',
        name: '相位',
        line: { color: '#ff7f0e', width: 2 }
    };
    const phaseLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)' },
        yaxis: { title: '相位 (度)' },
        margin: { t: 20, b: 40, l: 50, r: 20 },
        height: 300
    };
    Plotly.react('phase-plot', [phaseTrace], phaseLayout);
}