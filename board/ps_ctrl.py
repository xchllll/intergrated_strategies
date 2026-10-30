#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ps_ctrl.py - PS 侧硬件控制驱动（PYNQ-Z2 / Zynq-7000）【溢出防护版】

配合仓库 src/top.v 与 docs/register_map.md 使用：
通过 4 个 AXI GPIO 完成"设频 -> 设积分窗 -> 启动 -> 回读 I/Q"的完整测量闭环。

硬件映射（依据 docs/register_map.md，最新 XSA 已全链路接线）：
    axi_gpio_0  基地址 0x4120_0000
      +0x00 GPIO_DATA  (ch1, 32b) -> ps_phase_inc（频率控制字）
      +0x08 GPIO2_DATA (ch2,  1b) -> ps_start（启动信号）
    axi_gpio_1  基地址 0x4121_0000
      +0x00 GPIO_DATA  (ch1, 32b) -> ps_window_max（积分窗长）
      +0x08 GPIO2_DATA (ch2,  1b) <- out_done（测量完成标志）
    axi_gpio_2  基地址 0x4122_0000
      +0x00 GPIO_DATA  (ch1, 32b) <- out_acc_I_low [31:0]
      +0x08 GPIO2_DATA (ch2, 16b) <- out_acc_I_high[47:32]
    axi_gpio_3  基地址 0x4123_0000
      +0x00 GPIO_DATA  (ch1, 32b) <- out_acc_Q_low [31:0]
      +0x08 GPIO2_DATA (ch2, 16b) <- out_acc_Q_high[47:32]

频率公式（与 top.v / dds.v 一致）：
    phase_inc = round(f_out * 2^32 / FCLK)

积分窗口口径（与 python_model/calibration.py 统一）：
    N = ps_window_max = round(periods * AUDIO_SAMPLE_RATE_HZ / f_out)
    即积分窗覆盖 periods 个完整信号周期：混频产生的 2f 分量在整数周期内
    平均为 0，低频测量不失真；N 同时是 calibration 的归一化点数，由
    measure_once() 原样返回，调用方必须把它传给
    calibration.calc_mag_phase(acc_I, acc_Q, N)。
    FPGA_CLK_HZ 是 PL 系统时钟，AUDIO_SAMPLE_RATE_HZ 是 I2S 每声道
    样本率。当前 I2S 每帧 2048 个 FPGA 时钟，100 MHz 时采样率
    48828.125 Hz。解调累加与积分窗都按有效 ADC 样本计数。

【48 位累加器溢出防护】（v2 新增）
    demod.v 的 acc_I / acc_Q 是 48 位有符号累加器，满量程上限
    ACC_FULL_SCALE = 2^47 - 1 ≈ 1.407e14。同相满量程积分的最坏情况：
        acc ≈ N * (A_ADC * A_LO / 2) = N * (32767 * 32767 / 2) ≈ N * 5.37e8
    因此安全积分点数上限 SAFE_MAX_N ≈ 2.1e5（留 20% 裕量）。
    本驱动在 set_window()/measure_once() 中自动把 periods 降到安全上限
    之内（低频时 N 大，自动减周期；N 与归一化口径随实际窗长联动，精度
    不受影响，返回的 window_n 始终是实际积分点数）。
    注意：当频率低到"连 1 个完整周期都超过 SAFE_MAX_N"（满量程输入时
    约 f < 239 Hz）时，软件无法仅靠减周期完全消除溢出风险，会打印警告；
    此时应通过前端衰减/输入缩放降低 ADC 码值，或从硬件侧扩位宽/降本振。
    （曾评估"分块积分、软件求和"方案：分块必须覆盖整数周期才能让 2f
    分量平均为 0，而 f < 239 Hz 时单个完整周期已超安全上限，分块不成立，
    故不采用，仅保留自动减周期 + 告警。）

时序要点（top.v 行为）：
    1) RTL 已增加结果锁存：积分窗满后先经 FLUSH 补上最后一个样本，再进入
       DONE 并把 acc 搬进 latched_acc_I/Q；out_done 只在锁存有效后置起。
       此后即使 ps_start 被拉低（状态机回到 IDLE、demod 的累加器被清零），
       48 位结果也会一直保持到下一次测量启动时才清零。因此不再要求
       "必须在 start 仍为高电平时回读"——DONE 之后晚一点取也不会丢；
       measure_once() 依旧在 DONE 里直接取数，只是不再受这个时序约束。
    2) ps_window_max 必须在 ps_start 拉高之前写入：MEASURE 态在
       rx_data_valid 有效拍比较 sample_count + 1 >= ps_window_max，
       中途改窗长会破坏本次积分。
"""

import os
import time

try:
    from pynq import MMIO
except ImportError as _import_err:      # 非 PYNQ 环境（如 PC 预览）仅作导入保护
    MMIO = None
    _PYNQ_IMPORT_ERROR = _import_err
else:
    _PYNQ_IMPORT_ERROR = None

# ---------------------------------------------------------------------------
# 硬件地址常量（已按 docs/register_map.md 核实）
# ---------------------------------------------------------------------------
AXI_GPIO_0_BASE = 0x41200000   # 写：ps_phase_inc(ch1) + ps_start(ch2)
AXI_GPIO_1_BASE = 0x41210000   # 写：ps_window_max(ch1)；读：out_done(ch2)
AXI_GPIO_2_BASE = 0x41220000   # 读：out_acc_I_low(ch1) + out_acc_I_high(ch2)
AXI_GPIO_3_BASE = 0x41230000   # 读：out_acc_Q_low(ch1) + out_acc_Q_high(ch2)

REG_GPIO_DATA = 0x00           # channel 1 数据寄存器
REG_GPIO2_DATA = 0x08          # channel 2 数据寄存器

# 系统参数
FPGA_CLK_HZ = float(os.environ.get("PYNQ_FPGA_CLK_HZ", "100000000"))
if FPGA_CLK_HZ <= 0:
    raise ValueError("PYNQ_FPGA_CLK_HZ 必须为正数，当前为 %r" % FPGA_CLK_HZ)

# i2s_tx creates one stereo frame every 2048 FPGA clocks:
# 2 channels * 32 bits * 32 clocks/bit.
AUDIO_SAMPLE_RATE_HZ = float(
    os.environ.get("PYNQ_AUDIO_SAMPLE_RATE_HZ", str(FPGA_CLK_HZ / 2048.0))
)
if AUDIO_SAMPLE_RATE_HZ <= 0:
    raise ValueError(
        "PYNQ_AUDIO_SAMPLE_RATE_HZ 必须为正数，当前为 %r" % AUDIO_SAMPLE_RATE_HZ
    )

# Kept as a compatibility alias for existing imports.
SAMPLE_CLK = FPGA_CLK_HZ
REG_RANGE = 0x1000             # AXI GPIO 地址段长度（4096 字节）

# 测量参数
DEFAULT_PERIODS = 10           # 默认积分窗覆盖的信号周期数（整数）
PERIODS_BASE = DEFAULT_PERIODS
PERIODS_KNEE_HZ = 200.0
PERIODS_EXPONENT = 0.8
PERIODS_MAX = 500
SWEEP_TIME_BUDGET_S = 25.0

MIN_HOLD_S = 1e-3              # start 保持的最短时长（秒），留足状态机裕量

# ---------------------------------------------------------------------------
# 48 位累加器溢出防护参数（v2）
# ---------------------------------------------------------------------------
# demod.v 的 acc_I/acc_Q 为 48 位有符号累加器，满量程上限：
ACC_FULL_SCALE = (1 << 47) - 1                    # ≈ 1.407e14
LO_GAIN = 32767                                   # DDS 正弦表满量程（16 位）
ADC_FULL_SCALE_16BIT = 32767                      # 输入信号最坏情况（满量程 16 位码值）
# 同相满量程单点相干积分贡献 ≈ A_ADC * A_LO / 2；留 20% 裕量，得到安全点数上限：
SAFE_MAX_N = int(0.8 * ACC_FULL_SCALE / (ADC_FULL_SCALE_16BIT * LO_GAIN / 2.0))
# 仅靠减小 periods 即可保证安全的频率下限（periods=1 时 N 仍 ≤ SAFE_MAX_N）：
SAFE_MIN_FREQ_HZ = AUDIO_SAMPLE_RATE_HZ / SAFE_MAX_N

_mmio_cache = {}


def _get_mmio(base_addr, length=REG_RANGE):
    """获取（并缓存）指定基地址的 MMIO 句柄。"""
    if MMIO is None:
        raise RuntimeError(
            "无法导入 pynq.MMIO：请在 PYNQ 镜像中运行本脚本"
            "（pip install pynq）。import 错误：%s" % _PYNQ_IMPORT_ERROR)
    if base_addr not in _mmio_cache:
        _mmio_cache[base_addr] = MMIO(base_addr, length)
    return _mmio_cache[base_addr]


def _to_signed_48(value):
    """把 48 位无符号读数转换成 Python 有符号整数（补码）。"""
    value &= 0xFFFFFFFFFFFF
    return value - (1 << 48) if value >= (1 << 47) else value


def _safe_periods(freq_hz, periods):
    """把 periods 限制到 48 位累加器安全范围内（满量程最坏情况），返回 ≥1 的值。

    低频时单个信号周期的采样点数 N_period = AUDIO_SAMPLE_RATE_HZ/f 可能很大，
    必须压低 periods 使 N = periods * N_period ≤ SAFE_MAX_N，
    避免 acc_I/acc_Q 溢出 48 位有符号上限（溢出后数据不可用）。
    """
    n_per_period = AUDIO_SAMPLE_RATE_HZ / freq_hz
    safe_p = int(SAFE_MAX_N / n_per_period)   # 满足安全条件的最多整数周期数
    return max(1, min(periods, safe_p))


# ---------------------------------------------------------------------------
# 写侧：频率、积分窗、启动
# ---------------------------------------------------------------------------
def set_freq(freq_hz):
    """输入频率（Hz），换算 phase_inc 并写入 axi_gpio_0。

    返回实际写入的 phase_inc（32 位无符号整数），便于调试核对。
    """
    if freq_hz <= 0:
        raise ValueError("freq_hz 必须为正数，收到 %r" % freq_hz)
    if freq_hz >= AUDIO_SAMPLE_RATE_HZ / 2.0:
        raise ValueError(
            "freq_hz 必须小于音频采样率/2 = %.1f Hz（奈奎斯特限制）"
            % (AUDIO_SAMPLE_RATE_HZ / 2.0))

    phase_inc = int(round(freq_hz * (2.0 ** 32) / FPGA_CLK_HZ))
    phase_inc &= 0xFFFFFFFF

    _get_mmio(AXI_GPIO_0_BASE).write(REG_GPIO_DATA, phase_inc)
    return phase_inc


def set_window(freq_hz, periods=DEFAULT_PERIODS, auto_safe=True):
    """按"整数个信号周期"设置积分窗长并写入 axi_gpio_1。

    窗长 N = round(periods * AUDIO_SAMPLE_RATE_HZ / freq_hz)，表示解调积分窗覆盖
    periods 个完整信号周期（2f 分量在整数周期内平均为 0，低频精度有保证）。
    N 同时作为 calibration 的归一化点数，返回给调用方。

    auto_safe=True（默认）：自动把 periods 限制到 48 位累加器安全范围内
    （满量程最坏情况，见模块注释"溢出防护"），实际窗长 N 随之减小并原样
    返回，调用方与归一化口径无需感知。低频极限（连 1 个周期都超上限）
    会打印警告，此时需前端衰减或从硬件侧扩位宽/降本振。

    注意：必须在 ps_start 拉高之前调用；MEASURE 态中途改窗长无效。
    """
    if freq_hz <= 0:
        raise ValueError("freq_hz 必须为正数，收到 %r" % freq_hz)
    # 允许传入浮点（频率自适应调度给的就是浮点），四舍五入到整数周期
    try:
        periods = int(round(float(periods)))
    except (TypeError, ValueError):
        raise ValueError("periods 必须为正整数，收到 %r" % (periods,))
    if periods < 1:
        raise ValueError("periods 必须为正整数，收到 %r" % (periods,))

    used_periods = periods
    if auto_safe:
        used_periods = _safe_periods(freq_hz, periods)
        if used_periods < periods:
            print("WARNING: 为防 48bit 累加器溢出（满量程最坏情况），"
                  "积分周期数 %d -> %d（f=%.1f Hz）"
                  % (periods, used_periods, freq_hz))
        if used_periods == 1 and AUDIO_SAMPLE_RATE_HZ / freq_hz > SAFE_MAX_N:
            print("WARNING: f=%.1f Hz 时即使 1 个信号周期也超过 48bit 累加器"
                  "安全上限（满量程输入，N=%.0f > SAFE_MAX_N=%d）；"
                  "建议前端衰减/输入缩放，或硬件扩位宽/降本振。"
                  % (freq_hz, AUDIO_SAMPLE_RATE_HZ / freq_hz, SAFE_MAX_N))

    n = int(round(used_periods * AUDIO_SAMPLE_RATE_HZ / freq_hz))
    if n < 1:
        raise ValueError("积分窗长 N=%d < 1：请增大 periods 或降低频率" % n)
    if n > 0xFFFFFFFF:
        raise ValueError(
            "积分窗长 N=%d 超出 32 位范围：请减小 periods 或提高最低频率" % n)

    _get_mmio(AXI_GPIO_1_BASE).write(REG_GPIO_DATA, n)
    return n


def start_measure(duration_s=0.01):
    """拉高 start，保持 duration_s 秒后拉低（低层接口）。

    注意：调用前必须已写好 ps_window_max（set_window）。RTL 已增加结果锁存，
    start 拉低后 DONE 状态的结果仍会保持到下一次测量启动，因此不再要求
    "必须在 start 仍为高时回读"；如需一步完成测量并取数，请使用 measure_once()。
    """
    gpio = _get_mmio(AXI_GPIO_0_BASE)
    gpio.write(REG_GPIO2_DATA, 0x1)          # 拉高 start
    try:
        time.sleep(max(duration_s, MIN_HOLD_S))
    finally:
        gpio.write(REG_GPIO2_DATA, 0x0)      # 拉低 start


# ---------------------------------------------------------------------------
# 读侧：out_done、48 位 acc_I / acc_Q
# ---------------------------------------------------------------------------
def read_done():
    """读取 out_done（axi_gpio_1 ch2，1 位）：True 表示本次测量完成。"""
    return bool(_get_mmio(AXI_GPIO_1_BASE).read(REG_GPIO2_DATA) & 0x1)


def read_IQ():
    """读取 48 位 acc_I 和 acc_Q，返回两个 Python 有符号整数 (acc_I, acc_Q)。

    映射（register_map.md）：
        axi_gpio_2: ch1 = acc_I[31:0]；ch2 低 16 位 = acc_I[47:32]
        axi_gpio_3: ch1 = acc_Q[31:0]；ch2 低 16 位 = acc_Q[47:32]
    """
    gpio_i = _get_mmio(AXI_GPIO_2_BASE)
    gpio_q = _get_mmio(AXI_GPIO_3_BASE)

    acc_i_raw = ((gpio_i.read(REG_GPIO2_DATA) & 0xFFFF) << 32) \
        | (gpio_i.read(REG_GPIO_DATA) & 0xFFFFFFFF)
    acc_q_raw = ((gpio_q.read(REG_GPIO2_DATA) & 0xFFFF) << 32) \
        | (gpio_q.read(REG_GPIO_DATA) & 0xFFFFFFFF)

    return _to_signed_48(acc_i_raw), _to_signed_48(acc_q_raw)


def periods_for_freq(freq_hz, base=PERIODS_BASE, knee_hz=PERIODS_KNEE_HZ,
                     exponent=PERIODS_EXPONENT, max_periods=PERIODS_MAX):
    """单个频点应积分的周期数：不高于 knee 用 base，高于则按 (f/knee)^exp 增长。"""
    if freq_hz <= knee_hz:
        return float(base)
    return float(min(max_periods, base * (freq_hz / knee_hz) ** exponent))


def build_period_schedule(freqs, base=PERIODS_BASE, knee_hz=PERIODS_KNEE_HZ,
                          exponent=PERIODS_EXPONENT, max_periods=PERIODS_MAX,
                          budget_s=SWEEP_TIME_BUDGET_S, min_scale=0.2):
    """按频点给出每轮扫频用的周期数，并按时间预算压缩低频段。

    单点耗时约等于 periods / f，整轮耗时约等于 sum(periods_i / f_i)。
    若预测值超过 budget_s，就把 f <= knee_hz 的低频段 periods 整体缩小
    （不低于 base*min_scale 且至少 1 个周期），高频段保持不变。

    返回 (periods_list, info)，info 含 predicted_s / scale_low / min / max。
    """
    freqs = [float(f) for f in freqs]
    ps = [periods_for_freq(f, base, knee_hz, exponent, max_periods) for f in freqs]

    def _cost(arr):
        return sum(p / f for p, f in zip(arr, freqs))

    total = _cost(ps)
    scale = 1.0
    if budget_s and total > budget_s:
        low = [i for i, f in enumerate(freqs) if f <= knee_hz]
        low_cost = sum(ps[i] / freqs[i] for i in low)
        high_cost = total - low_cost
        if low_cost > 0:
            scale = max(min_scale, min(1.0, (budget_s - high_cost) / low_cost))
            for i in low:
                ps[i] = ps[i] * scale
            total = _cost(ps)
    ps = [max(1.0, round(p, 2)) for p in ps]
    return ps, {"predicted_s": total, "scale_low": scale,
                "min": min(ps) if ps else 0.0, "max": max(ps) if ps else 0.0}


def measure_once(freq_hz, periods=DEFAULT_PERIODS, margin_s=0.05,
                 poll_interval_s=0.002):
    """一步完成单频点测量：设频 -> 设窗 -> 启动 -> 等待 -> 回读 -> 停止。

    设窗时默认开启溢出防护（auto_safe，见 set_window）：低频点会自动
    减小 periods 到 48 位累加器安全范围内，window_n 返回实际积分点数，
    归一化口径不受影响。

    参数:
        margin_s:        超时裕量（秒）。默认 50 ms。Python 侧 MMIO 轮询
                         本身有开销，1 ms 裕量时 N=488（积分约 10 ms）的
                         timeout 只有 11 ms，会被轮询开销吃光而误报超时。
        poll_interval_s: out_done 轮询间隔（秒）。默认 2 ms，在 1000 Hz /
                         N=488 下既能及时看到 DONE，又比 1 ms 轮询减少
                         约一半的 CPU 唤醒/寄存器读取开销。

    返回: (acc_I, acc_Q, window_n)
        acc_I / acc_Q: 48 位有符号累加值
        window_n:      本次实际积分点数 N（== 写入的 ps_window_max），
                       调用方必须把它传给
                       calibration.calc_mag_phase(acc_I, acc_Q, N)
    """
    set_freq(freq_hz)
    window_n = set_window(freq_hz, periods)

    # 超时时间 = 积分时长 + 裕量；以 DONE 寄存器为准，不再盲等固定时长。
    timeout_s = max(window_n / AUDIO_SAMPLE_RATE_HZ + margin_s, MIN_HOLD_S)

    gpio = _get_mmio(AXI_GPIO_0_BASE)
    gpio.write(REG_GPIO2_DATA, 0x1)          # 拉高 start
    try:
        deadline = time.monotonic() + timeout_s
        done_seen = False
        while time.monotonic() < deadline:
            done_seen = read_done()
            if done_seen:
                acc_i, acc_q = read_IQ()
                return acc_i, acc_q, window_n
            time.sleep(poll_interval_s)
        raise RuntimeError(
            "等待 out_done 超时（timeout=%.3f s，N=%d，f=%.1f Hz，"
            "fs=%.1f Hz，out_done=%d）；"
            "请检查 bitstream、FCLK_CLK0、AXI GPIO 地址和 ps_start 接线。"
            % (timeout_s, window_n, freq_hz, AUDIO_SAMPLE_RATE_HZ,
               int(done_seen)))
    finally:
        gpio.write(REG_GPIO2_DATA, 0x0)      # 拉低 start -> 回 IDLE


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="PS 侧硬件控制驱动（PYNQ-Z2，溢出防护版）")
    parser.add_argument("--freq", type=float, default=1000.0,
                        help="测量频率 Hz（默认 1000）")
    parser.add_argument("--periods", type=int, default=DEFAULT_PERIODS,
                        help="积分窗覆盖的信号周期数（默认 %d）" % DEFAULT_PERIODS)
    args = parser.parse_args()

    print("溢出防护：SAFE_MAX_N=%d 点；仅靠减周期可保证安全的频率下限 ≈ %.0f Hz"
          % (SAFE_MAX_N, SAFE_MIN_FREQ_HZ))
    phase_inc = set_freq(args.freq)
    window_n = set_window(args.freq, args.periods)
    print("phase_inc = 0x%08X (%d)" % (phase_inc, phase_inc))
    print("ps_window_max = %d 个 ADC 样本 (%.3f ms)"
          % (window_n, window_n / AUDIO_SAMPLE_RATE_HZ * 1e3))
    try:
        acc_i, acc_q, n = measure_once(args.freq, args.periods)
        print("acc_I = %d" % acc_i)
        print("acc_Q = %d" % acc_q)
        print("N = %d（与 ps_window_max 一致）" % n)
    except RuntimeError as err:
        print("读取侧未就绪：%s" % err)
