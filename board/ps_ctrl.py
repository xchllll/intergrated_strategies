#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ps_ctrl.py - PS 侧硬件控制驱动（PYNQ-Z2 / Zynq-7000）【补全版】

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
    N = ps_window_max = round(periods * SAMPLE_CLK / f_out)
    即积分窗覆盖 periods 个完整信号周期：混频产生的 2f 分量在整数周期内
    平均为 0，低频测量不失真；N 同时是 calibration 的归一化点数，由
    measure_once() 原样返回，调用方必须把它传给
    calibration.calc_mag_phase(acc_I, acc_Q, N)。
    SAMPLE_CLK 是解调采样时钟：当前 top.v 中 demod 直接跑 100 MHz 系统
    时钟；将来接入 I2S RX（编解码器采样，如 48 kHz）时，把 SAMPLE_CLK
    改为编解码器采样率即可，公式不变。

时序要点（top.v 行为）：
    1) ps_start 拉低后状态机回到 IDLE，IDLE 态将解调累加器清零，
       因此必须在 start 仍为高电平时回读结果（measure_once 已处理）。
    2) ps_window_max 必须在 ps_start 拉高之前写入：MEASURE 态每个时钟
       都在比较 timer >= ps_window_max，中途改窗长会破坏本次积分。
"""

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
SAMPLE_CLK = 100e6             # 解调采样时钟（top.v 系统时钟 100 MHz）
REG_RANGE = 0x1000             # AXI GPIO 地址段长度（4096 字节）

# 测量参数
DEFAULT_PERIODS = 10           # 默认积分窗覆盖的信号周期数（整数）
MIN_HOLD_S = 1e-3              # start 保持的最短时长（秒），留足状态机裕量

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


# ---------------------------------------------------------------------------
# 写侧：频率、积分窗、启动
# ---------------------------------------------------------------------------
def set_freq(freq_hz):
    """输入频率（Hz），换算 phase_inc 并写入 axi_gpio_0。

    返回实际写入的 phase_inc（32 位无符号整数），便于调试核对。
    """
    if freq_hz <= 0:
        raise ValueError("freq_hz 必须为正数，收到 %r" % freq_hz)
    if freq_hz >= SAMPLE_CLK / 2.0:
        raise ValueError(
            "freq_hz 必须小于 SAMPLE_CLK/2 = %.1f Hz（奈奎斯特限制）"
            % (SAMPLE_CLK / 2.0))

    phase_inc = int(round(freq_hz * (2.0 ** 32) / SAMPLE_CLK))
    phase_inc &= 0xFFFFFFFF

    _get_mmio(AXI_GPIO_0_BASE).write(REG_GPIO_DATA, phase_inc)
    return phase_inc


def set_window(freq_hz, periods=DEFAULT_PERIODS):
    """按"整数个信号周期"设置积分窗长并写入 axi_gpio_1。

    窗长 N = round(periods * SAMPLE_CLK / freq_hz)，表示解调积分窗覆盖
    periods 个完整信号周期（2f 分量在整数周期内平均为 0，低频精度有保证）。
    N 同时作为 calibration 的归一化点数，返回给调用方。

    注意：必须在 ps_start 拉高之前调用；MEASURE 态中途改窗长无效。
    """
    if freq_hz <= 0:
        raise ValueError("freq_hz 必须为正数，收到 %r" % freq_hz)
    if not isinstance(periods, int) or periods < 1:
        raise ValueError("periods 必须为正整数，收到 %r" % (periods,))

    n = int(round(periods * SAMPLE_CLK / freq_hz))
    if n < 1:
        raise ValueError("积分窗长 N=%d < 1：请增大 periods 或降低频率" % n)
    if n > 0xFFFFFFFF:
        raise ValueError(
            "积分窗长 N=%d 超出 32 位范围：请减小 periods 或提高最低频率" % n)

    _get_mmio(AXI_GPIO_1_BASE).write(REG_GPIO_DATA, n)
    return n


def start_measure(duration_s=0.01):
    """拉高 start，保持 duration_s 秒后拉低（低层接口）。

    注意：调用前必须已写好 ps_window_max（set_window），且应在 start
    仍为高时回读结果；如需一步完成测量并取数，请使用 measure_once()。
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


def measure_once(freq_hz, periods=DEFAULT_PERIODS, margin_s=1e-3):
    """一步完成单频点测量：设频 -> 设窗 -> 启动 -> 等待 -> 回读 -> 停止。

    返回: (acc_I, acc_Q, window_n)
        acc_I / acc_Q: 48 位有符号累加值
        window_n:      本次实际积分点数 N（== 写入的 ps_window_max），
                       调用方必须把它传给
                       calibration.calc_mag_phase(acc_I, acc_Q, N)
    """
    set_freq(freq_hz)
    window_n = set_window(freq_hz, periods)

    # 等待时间 = 积分时长 + 裕量；DONE 态在 start 为高时保持，多等无害。
    hold_s = max(window_n / SAMPLE_CLK + margin_s, MIN_HOLD_S)

    gpio = _get_mmio(AXI_GPIO_0_BASE)
    gpio.write(REG_GPIO2_DATA, 0x1)          # 拉高 start
    try:
        time.sleep(hold_s)
        acc_i, acc_q = read_IQ()             # start 仍为高，结果未被清零
        return acc_i, acc_q, window_n
    finally:
        gpio.write(REG_GPIO2_DATA, 0x0)      # 拉低 start -> 回 IDLE


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="PS 侧硬件控制驱动（PYNQ-Z2，补全版）")
    parser.add_argument("--freq", type=float, default=1000.0,
                        help="测量频率 Hz（默认 1000）")
    parser.add_argument("--periods", type=int, default=DEFAULT_PERIODS,
                        help="积分窗覆盖的信号周期数（默认 %d）" % DEFAULT_PERIODS)
    args = parser.parse_args()

    phase_inc = set_freq(args.freq)
    window_n = set_window(args.freq, args.periods)
    print("phase_inc = 0x%08X (%d)" % (phase_inc, phase_inc))
    print("ps_window_max = %d 拍 (%.3f ms)" % (window_n, window_n / SAMPLE_CLK * 1e3))
    try:
        acc_i, acc_q, n = measure_once(args.freq, args.periods)
        print("acc_I = %d" % acc_i)
        print("acc_Q = %d" % acc_q)
        print("N = %d（与 ps_window_max 一致）" % n)
    except RuntimeError as err:
        print("读取侧未就绪：%s" % err)
