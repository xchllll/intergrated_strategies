document.addEventListener('DOMContentLoaded', () => {
    drawBode([], [], []);
    loadStatus();
});

async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        const result = await response.json();
        if (result.status === 'success') {
            applyStatus(result.source, result.calibration);
        }
    } catch (error) {
        setNotice('无法读取后端状态：' + error.message, true);
    }
}

function applyStatus(source, calibration) {
    const sourceBadge = document.getElementById('source-badge');
    sourceBadge.textContent = source.label;
    sourceBadge.className = 'badge ' + (source.is_real ? 'badge-real' : 'badge-demo');

    const calibrationBadge = document.getElementById('calibration-badge');
    calibrationBadge.textContent = calibration.calibrated ? '已加载校准表' : '未校准';
    calibrationBadge.className = 'badge ' + (calibration.calibrated ? 'badge-real' : 'badge-warning');

    document.getElementById('source-detail').textContent = source.detail;
    document.getElementById('calibration-detail').textContent = calibration.message || '状态未知';
}

async function startSweep() {
    const button = document.getElementById('start-btn');
    const bufferEnabled = document.getElementById('buffer-toggle').checked;

    button.disabled = true;
    button.textContent = '扫频中...';
    document.getElementById('ai-topo').textContent = '计算中...';
    document.getElementById('ai-conf').textContent = '--%';
    setNotice('正在读取数据并计算 Bode 曲线...', false);

    try {
        const response = await fetch('/api/start_sweep', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_buffer: bufferEnabled })
        });
        const result = await response.json();
        if (!response.ok || result.status !== 'success') {
            throw new Error(result.msg || '后端未返回有效结果');
        }

        applyStatus(result.source, result.calibration);
        drawBode(result.data.freqs, result.data.mag_db, result.data.phase_deg);
        renderAi(result.ai);

        if (result.warnings && result.warnings.length) {
            setNotice(result.warnings.join(' '), !result.source.is_real);
        } else {
            setNotice('扫频完成，共 ' + result.data.freqs.length + ' 个频点。', false);
        }
    } catch (error) {
        setNotice('测量失败：' + error.message, true);
        document.getElementById('ai-topo').textContent = '未完成';
        document.getElementById('ai-conf').textContent = '--%';
    } finally {
        button.disabled = false;
        button.textContent = '开始扫频测量';
    }
}

function renderAi(ai) {
    if (!ai) {
        document.getElementById('ai-topo').textContent = '模型未返回结果';
        document.getElementById('ai-conf').textContent = '--%';
        document.getElementById('ai-note').textContent = '请查看上方提示信息。';
        return;
    }
    document.getElementById('ai-topo').textContent = ai.label || '未知';
    document.getElementById('ai-conf').textContent =
        typeof ai.confidence === 'number' ? (ai.confidence * 100).toFixed(1) + '%' : '--%';
    document.getElementById('ai-note').textContent = ai.model_note || '';
}

function setNotice(message, isWarning) {
    const element = document.getElementById('notice');
    element.textContent = message;
    element.className = isWarning ? 'notice notice-warning' : 'notice';
}

function drawBode(freqs, magnitude, phase) {
    const magnitudeTrace = {
        x: freqs,
        y: magnitude,
        type: 'scatter',
        mode: 'lines+markers',
        name: '幅频',
        line: { color: '#1769e0', width: 2 },
        marker: { size: 3 }
    };
    const magnitudeLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
        yaxis: { title: '幅值 (dB)', gridcolor: '#e8edf3' },
        margin: { t: 12, b: 48, l: 58, r: 20 },
        height: 310,
        plot_bgcolor: 'transparent',
        paper_bgcolor: 'transparent'
    };
    Plotly.react('magnitude-plot', [magnitudeTrace], magnitudeLayout, {
        responsive: true,
        displaylogo: false
    });

    const phaseTrace = {
        x: freqs,
        y: phase,
        type: 'scatter',
        mode: 'lines+markers',
        name: '相频',
        line: { color: '#d95c14', width: 2 },
        marker: { size: 3 }
    };
    const phaseLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
        yaxis: { title: '相位 (度)', gridcolor: '#e8edf3' },
        margin: { t: 12, b: 48, l: 58, r: 20 },
        height: 310,
        plot_bgcolor: 'transparent',
        paper_bgcolor: 'transparent'
    };
    Plotly.react('phase-plot', [phaseTrace], phaseLayout, {
        responsive: true,
        displaylogo: false
    });
}
