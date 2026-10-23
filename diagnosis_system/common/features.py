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
    freqs = np.array(freqs)
    mag_db = np.array(mag_db)
    phase_deg = np.array(phase_deg)
    
    # 1. 低频与高频增益 (取最前和最后 5 个点的平均)
    gain_low = np.mean(mag_db[:5])
    gain_high = np.mean(mag_db[-5:])
    
    # 2. 增益斜率 (线性拟合，换算为 dB/dec)
    log_freqs = np.log10(freqs)
    slope, _ = np.polyfit(log_freqs, mag_db, 1)
    slope_db_dec = slope
    
    # 3. 截止频率 (-3dB点，相对低频增益)
    target_low_db = gain_low - 3.0
    # 寻找第一个低于 -3dB 点的索引
    idx_f3db = np.where(mag_db < target_low_db)[0]
    if len(idx_f3db) > 0:
        f3db_hz = freqs[idx_f3db[0]]
    else:
        f3db_hz = freqs[-1] # 没找到的话就取最高频
        
    # 4. 峰值特征
    peak_idx = np.argmax(mag_db)
    peak_gain_db = mag_db[peak_idx]
    peak_freq_hz = freqs[peak_idx]
    
    # 峰值突出度 (峰值增益减去两端增益的较大值)
    peak_prominence = peak_gain_db - max(gain_low, gain_high)
    
    # 5. Q值粗略估计 (中心频率 / 带宽)
    # 找比峰值低 3dB 的两个边界
    target_peak_db = peak_gain_db - 3.0
    valid_idx = np.where(mag_db >= target_peak_db)[0]
    if len(valid_idx) > 1:
        f_lower = freqs[valid_idx[0]]
        f_upper = freqs[valid_idx[-1]]
        bw = f_upper - f_lower
        q_factor = peak_freq_hz / bw if bw > 0 else 0.0
    else:
        q_factor = 0.0

    # 6. 相位特征
    phase_low = np.mean(phase_deg[:5])
    phase_high = np.mean(phase_deg[-5:])
    phase_mid = phase_deg[len(phase_deg)//2]
    phase_span = np.max(phase_deg) - np.min(phase_deg)
    
    # 7. 曲线平滑度 (二阶差分)
    # 平滑的曲线二阶差分很小，有毛刺或谐振峰的地方二阶差分大
    smoothness = np.sqrt(np.mean(np.diff(mag_db, n=2)**2))

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