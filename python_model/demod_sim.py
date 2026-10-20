import numpy as np
import math

# 1. 设定系统参数
fs = 100000       # Sample Frequency (采样频率)：每秒采集 10 万个点 (100kHz)
f_in = 1000       # Input Frequency (输入信号频率)：被测信号为 1kHz
N = 1000          # Number of points (积分点数)：1000 个点刚好是 1kHz 信号的一个完整周期

print(f"系统初始化完成：采样率 {fs} Hz,测试信号频率 {f_in} Hz,积分窗口 {N} 点。")

# 2. 生成时间轴 (Time Axis)
# np.arange(N) 生成 0 到 999 的序列，除以 fs 转换成真实的时间 t
t = np.arange(N) / fs

# 3. 生成理想测试信号 (Ideal Test Signal)
# 模拟被测信号：Amplitude (幅度)=1.5, Phase (相位)=pi/4 (45度)
signal_in = 1.5 * np.cos(2 * np.pi * f_in * t + np.pi/4)

# 打印前 5 个数据点，看看波形长什么样
print("前 5 个采样点的值:")
for i in range(5):
    print(f"时间 {t[i]:.5f} 秒, 信号值: {signal_in[i]:.4f}")

# 4. 生成本地参考信号 (Local Oscillator / Reference)
ref_cos = np.cos(2 * np.pi * f_in * t)      # I 路 (同相)
ref_sin = -np.sin(2 * np.pi * f_in * t)     # Q 路 (正交)

# 5. 混频 (Mixer) - 逐点相乘
mix_I = signal_in * ref_cos
mix_Q = signal_in * ref_sin

# 打印第 0 个点的混频结果，验证一下
print("\n--- 混频 (Mixer) 结果验证 ---")
print(f"信号第 0 点: {signal_in[0]:.4f}")
print(f"I 路参考第 0 点: {ref_cos[0]:.4f}")
print(f"I 路混频第 0 点 (两者相乘): {mix_I[0]:.4f}")

print(f"\nQ 路参考第 0 点: {ref_sin[0]:.4f}")
print(f"Q 路混频第 0 点 (两者相乘): {mix_Q[0]:.4f}")

# 6. 积分 (Integration) - 离散求和
# np.sum() 将所有点的值加起来
sum_I = np.sum(mix_I)
sum_Q = np.sum(mix_Q)

# 7. 归一化 (Normalization)
# 乘 2 是因为公式里有个 A/2，除以 N 是求平均值
I_float = sum_I * 2 / N
Q_float = sum_Q * 2 / N

# 8. 计算最终的幅值 (Magnitude) 和 相位 (Phase)
# math.sqrt() 是开根号，math.atan2() 是反正切（能自动处理象限）
mag_float = math.sqrt(I_float**2 + Q_float**2)
phase_float = math.degrees(math.atan2(Q_float, I_float))

print("\n--- 最终解调结果 ---")
print(f"I 路积分值: {I_float:.4f}")
print(f"Q 路积分值: {Q_float:.4f}")
print(f"计算出的幅值: {mag_float:.4f}  (理论值应为 1.5000)")
print(f"计算出的相位: {phase_float:.4f} 度 (理论值应为 45.0000)")

# ==========================================
# 8. 模拟 FPGA 里的定点化 (Fixed-point) 过程
# ==========================================
print("\n--- 开始进行定点化模拟 ---")

# 1. 模拟 ADC 采样：把浮点信号放大 10000 倍，变成 16 位整数
# np.round 是四舍五入，astype 是强制转换类型
adc_in = np.round(signal_in * 10000).astype(np.int32)

# 2. 模拟正弦表：幅度放大到 32767 (16位有符号数的最大值)
ref_cos_fixed = np.round(ref_cos * 32767).astype(np.int32)
ref_sin_fixed = np.round(ref_sin * 32767).astype(np.int32)

# 3. 【混频乘法】：16位(ADC) * 16位(本振) = 32位结果
mix_I_fixed = adc_in * ref_cos_fixed
mix_Q_fixed = adc_in * ref_sin_fixed

# 4. 【积分累加】：硬件里必须用更大的位宽来装结果。
# 最大值估算：15000 * 32767 * 1000 ≈ 4.9e11 (需要 39 位以上)
# 在 Python 里，我们强制用 64 位整型 (int64) 来模拟硬件的 48 位累加器
acc_I = np.sum(mix_I_fixed, dtype=np.int64)
acc_Q = np.sum(mix_Q_fixed, dtype=np.int64)

# 5. 【移位截断】：把放大的倍数除掉，还原真实大小
# 归一化因子 = 点数 * ADC放大倍数 * 本振放大倍数
norm_factor = N * 10000 * 32767
I_fixed = acc_I * 2 / norm_factor
Q_fixed = acc_Q * 2 / norm_factor

# 6. 算定点结果下的幅值和相位
mag_fixed = math.sqrt(I_fixed**2 + Q_fixed**2)
phase_fixed = math.degrees(math.atan2(Q_fixed, I_fixed))

print("\n--- 终极验收：定点仿真结果 ---")
print(f"定点计算幅值: {mag_fixed:.4f}")
print(f"定点计算相位: {phase_fixed:.4f} 度")