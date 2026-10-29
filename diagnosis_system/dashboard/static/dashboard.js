document.addEventListener('DOMContentLoaded', () => {
    drawBode([], [], [], 'demo_csv');
    loadStatus();
});

// 1. 修改 loadStatus，支持传参
async function loadStatus() {
    const isBufferOn = document.getElementById('buffer-toggle').checked;
    const calPath = isBufferOn ? 'buffer' : 'direct';
    
    try {
        const response = await fetch(`/api/status?cal_path=${calPath}`);
        const result = await response.json();
        if (result.status === 'success') {
            applyStatus(result.source, result.calibration);
        }
    } catch (error) {
        setNotice('无法读取后端状态：' + error.message, true);
    }
}

// 2. 在 DOMContentLoaded 里面，给开关加个监听事件 (加到 document.addEventListener 里面)
document.addEventListener('DOMContentLoaded', () => {
    drawBode([], [], [], 'demo_csv');
    loadStatus();
    
    // 【P2 修复】每次拨动开关，立刻重新拉取校准状态
    document.getElementById('buffer-toggle').addEventListener('change', () => {
        loadStatus();
    });
});

function applyStatus(source, calibration) {
    const sourceBadge = document.getElementById('source-badge');
    const calibrationBadge = document.getElementById('calibration-badge');

    // 根据 mode 精确设置 Badge
    if (source.mode === 'pynq_rtl') {
        sourceBadge.textContent = '自研 PL 解调实测';
        sourceBadge.className = 'badge badge-real'; // 绿色
    } else if (source.mode === 'pynq_audio_loopback') {
        sourceBadge.textContent = '官方音频闭环（仅幅值）';
        sourceBadge.className = 'badge badge-demo'; // 蓝色
    } else {
        sourceBadge.textContent = '演示数据';
        sourceBadge.className = 'badge badge-demo'; // 蓝色
    }

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
        // A2: 模式拆分，默认请求 rtl 自研模式
        const payload = {
            mode: "rtl", 
            cal_path: bufferEnabled ? "buffer" : "direct"
        };

        const response = await fetch('/api/start_sweep', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok || result.status !== 'success') {
            throw new Error(result.msg || '后端未返回有效结果');
        }

        applyStatus(result.source, result.calibration);
        
        // 把真实的 source.mode 传给画图函数
        drawBode(
            result.data.freqs, 
            result.data.mag_db, 
            result.data.phase_deg, 
            result.source.mode,
            result.data.phase_valid
        );
        
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

// B 核心逻辑：根据模式动态绘图
function drawBode(freqs, magnitude, phase, mode, phaseValid) {
    // 1. 动态判断坐标轴标题
    const isAudio = (mode === 'pynq_audio_loopback');
    const magYAxisTitle = isAudio ? '电平 (dBFS)' : '增益 (dB)';
    // 低信噪比频点的相位置空，Plotly 会断开该段，不再出现 0/±180 的假跳变
    const phaseY = (Array.isArray(phaseValid) && phaseValid.length === phase.length)
        ? phase.map((v, i) => (phaseValid[i] ? v : null))
        : phase;

    const magnitudeTrace = {
        x: freqs, y: magnitude, type: 'scatter', mode: 'lines+markers',
        name: '幅频', line: { color: '#1769e0', width: 2 }, marker: { size: 3 }
    };
    const magnitudeLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
        yaxis: { title: magYAxisTitle, gridcolor: '#e8edf3' }, // 使用动态标题
        margin: { t: 12, b: 48, l: 58, r: 20 }, height: 310,
        plot_bgcolor: 'transparent', paper_bgcolor: 'transparent'
    };
    Plotly.react('magnitude-plot', [magnitudeTrace], magnitudeLayout, { responsive: true, displaylogo: false });

    // 2. 相频图的置灰处理
    const phaseContainer = document.getElementById('phase-container');
    const phaseTitle = document.getElementById('phase-title');

    if (isAudio) {
        // audio 模式：置灰并显示警告
        phaseContainer.style.opacity = '0.4';
        phaseTitle.textContent = '相频特性（无相干参考，相位无效）';
        
        // 传入空数据，或者一条平线
        const phaseTrace = { x: freqs, y: [], type: 'scatter' };
        Plotly.react('phase-plot', [phaseTrace], {
            xaxis: { title: '频率 (Hz)' },
            yaxis: { title: '相位 (度)', range: [-180, 180] },
            annotations: [{
                text: '官方音频闭环无物理相位参考',
                xref: 'paper', yref: 'paper', x: 0.5, y: 0.5, showarrow: false,
                font: { size: 16, color: 'red' }
            }],
            margin: { t: 12, b: 48, l: 58, r: 20 }, height: 310,
            plot_bgcolor: 'transparent', paper_bgcolor: 'transparent'
        }, { responsive: true, displaylogo: false });
        
    } else {
        // rtl 或 demo 模式：恢复正常显示
        phaseContainer.style.opacity = '1';
        phaseTitle.textContent = '相频特性';
        
        const phaseTrace = {
            x: freqs, y: phaseY, type: 'scatter', mode: 'lines+markers',
            name: '相频', line: { color: '#d95c14', width: 2 }, marker: { size: 3 }
        };
        const phaseLayout = {
            xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
            yaxis: { title: '相位 (度)', gridcolor: '#e8edf3' },
            margin: { t: 12, b: 48, l: 58, r: 20 }, height: 310,
            plot_bgcolor: 'transparent', paper_bgcolor: 'transparent'
        };
        Plotly.react('phase-plot', [phaseTrace], phaseLayout, { responsive: true, displaylogo: false });
    }
}