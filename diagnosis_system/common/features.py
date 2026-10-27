import numpy as np

# 定义模型需要的 13 个特征名称，固定顺序
FEATURE_ORDER = [
    "gain_low",        # 低频增益 (dB)
    "gain_high",       # 高频增益 (dB)
    "slope_db_dec",    # 增益斜率 (dB/dec)
    "f3db_hz",         # 截止频率 (-3dB点)
    "peak_freq_hz",    # 峰值频率 (Hz)
    "peak_gain_db",    # 峰值增益 (dB)
    "peak_prominence", # 峰值突出度
    "q_factor",        # Q值估计
    "phase_low",       # 低频相位 (度)
    "phase_high",      # 高频相位 (度)
    "phase_mid",       # 中频相位 (度)
    "phase_span",      # 相位跨度
    "smoothness"       # 曲线平滑度 (二阶差分RMS)
]

def extract_features(freqs, mag_db, phase_deg):
    """
    输入: 
        freqs: 频率数组 (Hz)
        mag_db: 增益数组 (dB)
        phase_deg: 相位数组 (度)
    输出: 
        包含 13 个特征的字典
    """
    freqs = np.asarray(freqs)
    mag_db = np.asarray(mag_db)
    phase_deg = np.asarray(phase_deg)
    
    n_pts = len(mag_db)
    if n_pts == 0:
        return {k: 0.0 for k in FEATURE_ORDER}

    # 【修复3】动态边缘取点：12个点时取1个，240个点时取5个，防止小数据被吃光
    edge_idx = max(1, min(5, n_pts // 10))
    
    # 1. 低频与高频增益
    gain_low = np.mean(mag_db[:edge_idx])
    gain_high = np.mean(mag_db[-edge_idx:])
    
    # 2. 增益斜率 (线性拟合，换算为 dB/dec)
    log_freqs = np.log10(freqs)
    slope, _ = np.polyfit(log_freqs, mag_db, 1)
    slope_db_dec = slope
    
    # 4. 峰值特征 (提前算，给 f3db 用)
    peak_idx = np.argmax(mag_db)
    peak_gain_db = mag_db[peak_idx]
    peak_freq_hz = freqs[peak_idx]
    peak_prominence = peak_gain_db - max(gain_low, gain_high)
    
    # 【修复1】截止频率 (-3dB点) 通用算法
    # 先确定通带的参考增益（取两端或峰值中的最大值）
    ref_gain = max(gain_low, gain_high, peak_gain_db)
    target_gain = ref_gain - 3.0
    
    if gain_low > gain_high:
        # 类似低通：寻找从高往下穿越 -3dB 的点
        idx_f3db = np.where(mag_db <= target_gain)[0]
        f3db_hz = freqs[idx_f3db[0]] if len(idx_f3db) > 0 else freqs[-1]
    else:
        # 类似高通或带通：寻找从低往上穿过 -3dB 的点
        idx_f3db = np.where(mag_db >= target_gain)[0]
        f3db_hz = freqs[idx_f3db[0]] if len(idx_f3db) > 0 else freqs[0]
        
    # 5. Q值粗略估计 (中心频率 / 带宽)
    target_peak_db = peak_gain_db - 3.0
    valid_idx = np.where(mag_db >= target_peak_db)[0]
    if len(valid_idx) > 1:
        f_lower = freqs[valid_idx[0]]
        f_upper = freqs[valid_idx[-1]]
        bw = f_upper - f_lower
        q_factor = peak_freq_hz / bw if bw > 0 else 0.0
    else:
        q_factor = 0.0

    # 【修复2】相位特征 (解卷绕/Unwrap，解决 -180 到 180 的跳变)
    phase_unwrapped = np.degrees(np.unwrap(np.radians(phase_deg)))
    
    phase_low = np.mean(phase_unwrapped[:edge_idx])
    phase_high = np.mean(phase_unwrapped[-edge_idx:])
    phase_mid = phase_unwrapped[n_pts // 2]
    phase_span = np.max(phase_unwrapped) - np.min(phase_unwrapped)
    
    # 7. 曲线平滑度 (二阶差分)
    if n_pts > 2:
        smoothness = np.sqrt(np.mean(np.diff(mag_db, n=2)**2))
    else:
        smoothness = 0.0

    return {
        "gain_low": float(gain_low),
        "gain_high": float(gain_high),
        "slope_db_dec": float(slope_db_dec),
        "f3db_hz": float(f3db_hz),
        "peak_freq_hz": float(peak_freq_hz),
        "peak_gain_db": float(peak_gain_db),
        "peak_prominence": float(peak_prominence),
        "q_factor": float(q_factor),
        "phase_low": float(phase_low),
        "phase_high": float(phase_high),
        "phase_mid": float(phase_mid),
        "phase_span": float(phase_span),
        "smoothness": float(smoothness)
    }

def to_vector(feature_dict):
    """把特征字典转换为 numpy 数组，供机器学习模型使用"""
    return np.array([feature_dict[k] for k in FEATURE_ORDER])