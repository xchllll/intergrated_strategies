import numpy as np

def get_mock_sweep():
    """
    生成一组模拟的 RLC 带通滤波器数据
    参数: R=30, L=0.01H, C=1.15e-7F (fc ≈ 4695Hz, Q ≈ 10)
    """
    freqs = np.logspace(np.log10(20), np.log10(20000), 240) # 20Hz 到 20kHz，240个点
    s = 1j * 2 * np.pi * freqs
    R, L, C = 30, 0.01, 1.15e-7
    
    # 带通传递函数: H = sRC / (s^2 LC + sRC + 1)
    H = (s * R * C) / (s**2 * L * C + s * R * C + 1)
    
    mag_db = 20 * np.log10(np.abs(H))
    phase_deg = np.angle(H, deg=True)
    
    # 加一点随机噪声，让它看起来像真实硬件采的
    mag_db += np.random.normal(0, 0.05, len(freqs))
    phase_deg += np.random.normal(0, 0.3, len(freqs))
    
    return {
        "freqs": freqs.tolist(),
        "mag_db": mag_db.tolist(),
        "phase_deg": phase_deg.tolist()
    }