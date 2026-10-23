import numpy as np

# 定义 6 种类别，以及它们的显示名称
CLASSES = {
    "rc_lowpass": "RC 低通",
    "rc_highpass": "RC 高通",
    "rl_lowpass": "RL 低通",
    "rl_highpass": "RL 高通",
    "rlc_bandpass": "RLC 带通",
    "opamp_lowpass": "运放有源低通"
}

def build_sweeps(n_per_class=30, seed=42):
    """
    生成 6 类电路的假数据。
    n_per_class: 每种电路生成多少组数据
    返回: 迭代器，每次产出 (sweep_dict, label_str)
    """
    np.random.seed(seed)
    
    # 20Hz 到 20kHz 的 240 个对数分布频点，作为所有数据的标准 x 轴
    freqs = np.logspace(np.log10(20), np.log10(20000), 240)
    s = 1j * 2 * np.pi * freqs # 拉普拉斯变量 s = jw
    
    # 给硬件底噪准备的参数
    noise_mag_std = 0.05  # 幅值噪声 (dB)
    noise_phase_std = 0.3 # 相位噪声 (度)
    
    for label in CLASSES.keys():
        for _ in range(n_per_class):
            # 随机生成电路参数，保证曲线落在频段内
                        # 修改：让不同类别的电路有不同的参数倾向
            if label in ["rc_lowpass", "rc_highpass"]:
                fc = np.random.uniform(100, 2000) # RC 频率偏低
            elif label in ["rl_lowpass", "rl_highpass"]:
                fc = np.random.uniform(2000, 8000) # RL 频率偏高
            else:
                fc = np.random.uniform(500, 5000) # 带通和运放居中
                
            wc = 2 * np.pi * fc
            
            if label == "rc_lowpass":
                H = 1 / (1 + s / wc)
                
            elif label == "rc_highpass":
                H = (s / wc) / (1 + s / wc)
                
            elif label == "rl_lowpass":
                # 引入电感内阻 DCR 造成的低频分压衰减
                dcr_gain = np.random.uniform(0.8, 1.0) 
                H = dcr_gain / (1 + s / wc)
                
            elif label == "rl_highpass":
                # 引入高频无法完全透传的衰减
                hf_gain = np.random.uniform(0.8, 1.0)
                H = hf_gain * (s / wc) / (1 + s / wc)
                
            elif label == "rlc_bandpass":
                Q = np.random.uniform(2, 15)
                s_norm = s / wc
                H = s_norm / (s_norm**2 + (1/Q)*s_norm + 1)
                
            elif label == "opamp_lowpass":
                A0_db = np.random.uniform(20, 40)
                A0 = 10**(A0_db / 20)
                H = A0 / (1 + s / wc)
            
            # 计算幅值和相位
            mag_db = 20 * np.log10(np.abs(H))
            phase_deg = np.angle(H, deg=True)
            
            # 模拟真实硬件测量的缺陷：加一点点底噪和微小的偏置
            mag_db += np.random.normal(0, noise_mag_std, len(freqs))
            phase_deg += np.random.normal(0, noise_phase_std, len(freqs))
            mag_db += np.random.uniform(-0.2, 0.2)
            phase_deg += np.random.uniform(-1.0, 1.0)
            
            sweep_dict = {
                "freqs": freqs.tolist(),
                "mag_db": mag_db.tolist(),
                "phase_deg": phase_deg.tolist()
            }
            
            # 产出数据字典和对应的标签
            yield sweep_dict, label