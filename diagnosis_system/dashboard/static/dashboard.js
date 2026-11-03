document.addEventListener('DOMContentLoaded', () => {
    drawBode([], [], [], 'demo_csv');
    loadStatus();
});

// 1. 修改 loadStatus，支持传参
async function loadStatus() {
    const calPath = 'direct';   // 有源运放方案已放弃，固定走直连校准
    
    try {
        const response = await fetch(`/api/status?cal_path=${calPath}`);
        const result = await response.json();
        if (result.status === 'success') {
            applyStatus(result.source, result.calibration);
            applyShortCalibrationStatus(result.short_calibration);
            applySweepStatus(result.sweep);
        }
    } catch (error) {
        setNotice('无法读取后端状态：' + error.message, true);
    }
}

// 短接校准运行时轮询状态：禁用扫频按钮并显示进度
document.addEventListener('DOMContentLoaded', () => {
    setInterval(loadStatus, 2000);
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

const CAL_IDLE_TEXT = '\u4e00\u952e\u77ed\u63a5\u6821\u51c6';
const CAL_BG_BUSY = 'linear-gradient(135deg,#c98a1e,#9c6a12)';
const CAL_BG_OK = 'linear-gradient(135deg,#2b8a5a,#1f6e47)';
const CAL_BG_ERR = 'linear-gradient(135deg,#c0392b,#96281b)';
let __calHoldUntil = 0;
let __calLastErr = null;
let __calDoneShown = false;

function _calBtn() {
    return document.getElementById('cal-btn');
}

function _calSet(text, disabled, bg) {
    const b = _calBtn();
    if (!b) return;
    b.textContent = text;
    b.disabled = !!disabled;
    b.style.background = bg || '';
}

function applyShortCalibrationStatus(shortCal) {
    window.__lastShortCal = shortCal || {};
    const startBtn = document.getElementById('start-btn');
    const running = !!(shortCal && shortCal.running);
    const err = (shortCal && shortCal.error) || '';
    const done = !!(shortCal && shortCal.done);
    const sweepRunning = !!(window.__lastSweep && window.__lastSweep.running);

    if (startBtn) {
        if (running) {
            startBtn.disabled = true;
            startBtn.textContent = '\u77ed\u63a5\u6821\u51c6\u4e2d...';
            startBtn.title = shortCal.message || '';
        } else if (startBtn.textContent === '\u77ed\u63a5\u6821\u51c6\u4e2d...') {
            startBtn.disabled = false;
            startBtn.textContent = '\u5f00\u59cb\u626b\u9891\u6d4b\u91cf';
            startBtn.title = '';
        }
    }

    if (running) {
        const total = shortCal.total || 0;
        const pct = total ? Math.round((shortCal.progress || 0) * 100 / total) : 0;
        _calSet('\u6821\u51c6\u4e2d ' + pct + '%', true, CAL_BG_BUSY);
        setNotice(shortCal.message || '\u6b63\u5728\u77ed\u63a5\u6821\u51c6\uff0c\u8bf7\u4fdd\u6301\u77ed\u63a5...', false);
        return;
    }

    // 失败（可能在第一次轮询前就结束，所以按"错误内容变化"触发，而不是按 running 跳变）
    if (err) {
        if (err !== __calLastErr) {
            __calLastErr = err;
            const notShorted = /(\u4e0d\u662f\u77ed\u63a5|\u77ed\u63a5\u662f\u5426|\u5e45\u5ea6\u6bd4|\u5e45\u503c\u4e3a 0)/.test(err);
            _calSet(notShorted ? '\u7535\u8def\u672a\u77ed\u63a5' : '\u6821\u51c6\u5931\u8d25', false, CAL_BG_ERR);
            setNotice((notShorted ? '\u7535\u8def\u672a\u77ed\u63a5\uff0c\u8bf7\u63a5\u597d\u77ed\u63a5\u7ebf\u540e\u91cd\u8bd5\u3002' : '\u6821\u51c6\u5931\u8d25\uff1a') + err, true);
            __calHoldUntil = Date.now() + 10000;
        }
        if (__calHoldUntil && Date.now() < __calHoldUntil) {
            return;
        }
        _calSet(CAL_IDLE_TEXT, sweepRunning, sweepRunning ? CAL_BG_BUSY : '');
        return;
    }

    if (done) {
        if (!__calDoneShown) {
            __calDoneShown = true;
            _calSet('\u6821\u51c6\u5b8c\u6bd5 \u2713', false, CAL_BG_OK);
            setNotice('\u6821\u51c6\u5b8c\u6bd5\uff0c\u6821\u51c6\u8868\u5df2\u66f4\u65b0\u3002', false);
            __calHoldUntil = Date.now() + 10000;
        }
        if (__calHoldUntil && Date.now() < __calHoldUntil) {
            return;
        }
        _calSet(CAL_IDLE_TEXT, sweepRunning, sweepRunning ? CAL_BG_BUSY : '');
        return;
    }

    if (__calHoldUntil && Date.now() < __calHoldUntil) {
        return;
    }
    _calSet(CAL_IDLE_TEXT, sweepRunning, sweepRunning ? CAL_BG_BUSY : '');
}

function applySweepStatus(sweep) {
    window.__lastSweep = sweep || {};
    const startBtn = document.getElementById('start-btn');
    const calBtn = document.getElementById('cal-btn');
    const running = !!(sweep && sweep.running);
    if (running) {
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.textContent = '扫频中 ' + (sweep.progress || 0) + '/' +
                                   (sweep.total || 240);
            startBtn.title = sweep.message || '扫频测量进行中';
        }
        if (calBtn) calBtn.disabled = true;
        setNotice(sweep.message || ('扫频中 ' + (sweep.progress || 0) + '/' +
                  (sweep.total || 240) + '...'), false);
    } else if (sweep && sweep.error) {
        setNotice('扫频失败：' + sweep.error, true);
    } else if (sweep && sweep.done) {
        setNotice('扫频完成（' + (sweep.total || 240) + '/' +
                  (sweep.total || 240) + '）。', false);
    }
}

async function openShortCalibration() {
    const btn = _calBtn();
    if (btn && btn.disabled) return;
    if (window.__lastShortCal && window.__lastShortCal.running) {
        _calSet('\u6821\u51c6\u4e2d...', true, CAL_BG_BUSY);
        return;
    }
    if (window.__lastSweep && window.__lastSweep.running) {
        _calSet('\u626b\u9891\u4e2d\uff0c\u8bf7\u7a0d\u5019', true, CAL_BG_BUSY);
        setTimeout(function () { _calSet(CAL_IDLE_TEXT, false, ''); }, 3000);
        return;
    }
    __calHoldUntil = 0;
    __calLastErr = null;
    __calDoneShown = false;
    _calSet('\u68c0\u6d4b\u4e2d...', true, CAL_BG_BUSY);
    setNotice('\u6b63\u5728\u68c0\u67e5\u662f\u5426\u77ed\u63a5\u5e76\u542f\u52a8\u6821\u51c6...', false);
    try {
        const response = await fetch('/api/start_short_calibration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repeat: 3 })
        });
        const result = await response.json();
        if (!response.ok || result.status !== 'started') {
            throw new Error(result.msg || '\u540e\u7aef\u672a\u542f\u52a8\u6821\u51c6');
        }
        _calSet('\u6821\u51c6\u4e2d 0%', true, CAL_BG_BUSY);
    } catch (error) {
        _calSet('\u542f\u52a8\u5931\u8d25', false, CAL_BG_ERR);
        setNotice('\u6821\u51c6\u542f\u52a8\u5931\u8d25\uff1a' + error.message, true);
        __calHoldUntil = Date.now() + 6000;
    }
    loadStatus();
}

function closeShortCalibration() {
    const modal = document.getElementById('cal-modal');
    if (modal) modal.hidden = true;
}

async function confirmShortCalibration() {
    const input = document.getElementById('cal-repeat');
    const error = document.getElementById('cal-modal-error');
    const confirmBtn = document.getElementById('cal-confirm');
    const repeat = parseInt(input ? input.value : '', 10);
    if (!Number.isInteger(repeat) || repeat < 1 || repeat > 10) {
        if (error) error.textContent = '扫频轮次必须是 1~10 的整数。';
        return;
    }
    if (confirmBtn) confirmBtn.disabled = true;
    if (error) error.textContent = '';
    try {
        const response = await fetch('/api/start_short_calibration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repeat })
        });
        const result = await response.json();
        if (!response.ok || result.status !== 'started') {
            throw new Error(result.msg || '后端未启动短接校准');
        }
        closeShortCalibration();
        setNotice('一键短接校准已启动（' + repeat + ' 轮），请保持短接，约 ' +
                  (repeat * 20) + ' 秒...', false);
        loadStatus();
    } catch (err) {
        if (error) error.textContent = '启动失败：' + err.message;
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeShortCalibration();
});

async function startSweep() {
    const button = document.getElementById('start-btn');
    const calBtn = document.getElementById('cal-btn');
    const dutTypeSelect = document.getElementById('dut-type');
    const dutType = dutTypeSelect ? dutTypeSelect.value : 'series';

    if (window.__lastShortCal && window.__lastShortCal.running) {
        setNotice('正在进行一键短接校准，请稍候...', false);
        return;
    }
    if (window.__lastSweep && window.__lastSweep.running) {
        setNotice('扫频测量正在进行，请稍候...', false);
        return;
    }

    window.__lastSweep = {
        running: true,
        done: false,
        progress: 0,
        total: 240,
        message: '准备扫频...'
    };
    button.disabled = true;
    button.textContent = '扫频中 0/240';
    if (dutTypeSelect) dutTypeSelect.disabled = true;
    if (calBtn) calBtn.disabled = true;
    document.getElementById('ai-topo').textContent = '计算中...';
    document.getElementById('ai-conf').textContent = '--%';
    setNotice('正在读取数据并计算 Bode 曲线...', false);

    try {
        // A2: 模式拆分，默认请求 rtl 自研模式
        const payload = {
            mode: "rtl",
            cal_path: "direct",
            dut_type: dutType
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
            result.source.mode
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
        window.__lastSweep = Object.assign({}, window.__lastSweep, {
            running: false
        });
        button.disabled = false;
        button.textContent = '开始扫频测量';
        button.title = '';
        if (dutTypeSelect) dutTypeSelect.disabled = false;
        if (calBtn && !(window.__lastShortCal && window.__lastShortCal.running)) {
            calBtn.disabled = false;
        }
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
const TRUST_FMAX_HZ = 3000;

// 3 kHz 以上夹具寄生主导：这一段画成灰色虚线，其余保持实线
function __splitByBand(xs, ys) {
    const out = { xa: [], ya: [], xb: [], yb: [] };
    if (!xs || !xs.length) return out;
    const fmax = Math.max.apply(null, xs);
    if (!(fmax > TRUST_FMAX_HZ)) { out.xa = xs; out.ya = ys; return out; }
    let idx = 0;
    while (idx < xs.length && xs[idx] <= TRUST_FMAX_HZ) idx++;
    for (let i = 0; i <= idx && i < xs.length; i++) { out.xa.push(xs[i]); out.ya.push(ys[i]); }
    for (let i = idx; i < xs.length; i++) { out.xb.push(xs[i]); out.yb.push(ys[i]); }
    return out;
}

function drawBode(freqs, magnitude, phase, mode) {
    // 1. 动态判断坐标轴标题
    const isAudio = (mode === 'pynq_audio_loopback');
    const magYAxisTitle = isAudio ? '电平 (dBFS)' : '增益 (dB)';

    const __mSplit = __splitByBand(freqs, magnitude);
    const magnitudeTrace = {
        x: __mSplit.xa, y: __mSplit.ya, type: 'scatter', mode: 'lines+markers',
        name: '幅频', line: { color: '#1769e0', width: 2 }, marker: { size: 3 }
    };
    const magnitudeTraceTail = {
        x: __mSplit.xb, y: __mSplit.yb, type: 'scatter', mode: 'lines',
        name: '> 3 kHz 寄生影响增大',
        line: { color: '#9aa7b5', width: 2, dash: 'dash' }
    };
    const magnitudeLayout = {
        xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
        yaxis: { title: magYAxisTitle, gridcolor: '#e8edf3' }, // 使用动态标题
        margin: { t: 34, b: 48, l: 58, r: 20 }, height: 310,
        legend: { orientation: 'h', x: 0, y: 1.16, font: { size: 11 } },
        plot_bgcolor: 'transparent', paper_bgcolor: 'transparent'
    };
    Plotly.react('magnitude-plot', [magnitudeTrace, magnitudeTraceTail], magnitudeLayout, { responsive: true, displaylogo: false });

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
        
        const __pSplit = __splitByBand(freqs, phase);
        const phaseTrace = {
            x: __pSplit.xa, y: __pSplit.ya, type: 'scatter', mode: 'lines+markers',
            name: '相频', line: { color: '#d95c14', width: 2 }, marker: { size: 3 }
        };
        const phaseTraceTail = {
            x: __pSplit.xb, y: __pSplit.yb, type: 'scatter', mode: 'lines',
            name: '> 3 kHz \u5bc4\u751f\u5f71\u54cd\u589e\u5927',
            line: { color: '#c0a894', width: 2, dash: 'dash' }
        };
        const phaseLayout = {
            xaxis: { type: 'log', title: '频率 (Hz)', gridcolor: '#e8edf3' },
            yaxis: { title: '相位 (度)', gridcolor: '#e8edf3' },
            margin: { t: 34, b: 48, l: 58, r: 20 }, height: 310,
            legend: { orientation: 'h', x: 0, y: 1.16, font: { size: 11 } },
            plot_bgcolor: 'transparent', paper_bgcolor: 'transparent'
        };
        Plotly.react('phase-plot', [phaseTrace, phaseTraceTail], phaseLayout, { responsive: true, displaylogo: false });
    }
}