#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ps_ctrl.py - PS 侧硬件控制驱动（PYNQ-Z2 / Zynq-7000）

配合仓库 src/top.v 使用：通过 AXI GPIO 控制 FPGA 启动一次测量并回读结果。

已核实的硬件映射（来源：board/add_axi.xsa -> design_1.hwh / design_1.bda）：
    axi_gpio_0  基地址 0x4120_0000（挂载于 PS M_AXI_GP0，地址段 0x41200000-0x4120FFFF）
      +0x00  GPIO_DATA   (channel 1, 32 位) -> ps_phase_inc（频率控制字）
      +0x04  GPIO_TRI    (channel 1 方向，PL 内连不影响，保持默认)
      +0x08  GPIO2_DATA  (channel 2, 1 位)  -> ps_start（启动信号，bit0）
      +0x0C  GPIO2_TRI   (channel 2 方向)

频率公式（与 top.v / dds.v 一致）：
    phase_inc = round(f_out * 2^32 / FCLK)，FCLK = 100 MHz

硬件注意（重要）：
    1) 当前仓库 add_axi.xsa 的 Block Design 中，top.v 的 out_acc_I[47:0]、
       out_acc_Q[47:0]、out_done 尚未接入任何 AXI GPIO（端口悬空）。
       read_IQ() 需要先在 Block Design 中补接读取通道，建议：
           axi_gpio_1（双通道）：channel1 -> out_acc_I[31:0]
                                 channel2 -> out_acc_I[47:32]（占低 16 位）
           axi_gpio_2（双通道）：channel1 -> out_acc_Q[31:0]
                                 channel2 -> out_acc_Q[47:32]（占低 16 位）
       接入后把真实基地址填入下方 ACC_I_GPIO_BASE / ACC_Q_GPIO_BASE 即可。
    2) top.v 中 ps_start 拉低后状态机回到 IDLE，IDLE 态将 wire_sweep_clear 拉高，
       demod 累加器随即清零。因此必须在 start 仍为高电平时读取结果，
       推荐使用 measure_once() 一步完成"设频 -> 启动 -> 读取 -> 停止"。
"""

import time

try:
    from pynq import MMIO
except ImportError as _import_err:   # 非 PYNQ 环境（如 PC 预览）下仅作导入保护
    MMIO = None
    _PYNQ_IMPORT_ERROR = _import_err
else:
    _PYNQ_IMPORT_ERROR = None

# ---------------------------------------------------------------------------
# 硬件地址常量（已核实）
# ---------------------------------------------------------------------------
AXI_GPIO_0_BASE = 0x41200000    # axi_gpio_0 写侧：频率控制字 + 启动信号
REG_GPIO_DATA = 0x00            # channel 1 数据寄存器  -> ps_phase_inc[31:0]
REG_GPIO2_DATA = 0x08           # channel 2 数据寄存器  -> ps_start（bit0）

# 读取侧基地址（需按上述说明补接 GPIO 后填写真实地址）
ACC_I_GPIO_BASE = None          # 例如 0x41210000
ACC_Q_GPIO_BASE = None          # 例如 0x41220000

# 系统参数
FCLK = 100e6                    # top.v 系统时钟 100 MHz
REG_RANGE = 0x1000              # AXI GPIO 地址段长度（4096 字节）

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


def set_freq(freq_hz):
    """输入频率（Hz），内部换算 phase_inc 并写入硬件。

    参数:
        freq_hz: 目标频率，范围 (0, FCLK/2) Hz，受奈奎斯特限制。
    返回:
        实际写入的 phase_inc（32 位无符号整数），便于调试核对。
    """
    if freq_hz <= 0:
        raise ValueError("freq_hz 必须为正数，收到 %r" % freq_hz)
    if freq_hz >= FCLK / 2.0:
        raise ValueError("freq_hz 必须小于 FCLK/2 = %.1f Hz（奈奎斯特限制）" % (FCLK / 2.0))

    phase_inc = int(round(freq_hz * (2.0 ** 32) / FCLK))
    phase_inc &= 0xFFFFFFFF

    _get_mmio(AXI_GPIO_0_BASE).write(REG_GPIO_DATA, phase_inc)
    return phase_inc


def start_measure(duration_ms=2.0):
    """拉高 start，等待指定毫秒，拉低 start。

    参数:
        duration_ms: 保持 start 为高的时间（毫秒）。top.v 积分窗口固定为
            10000 拍 @100MHz = 100us，建议 duration_ms >= 1 以确保完成测量。
    注意:
        start 拉低后状态机回到 IDLE 并清零累加器，结果请在 start 仍为高时
        读取；如需一步完成测量并取数，请使用 measure_once()。
    """
    gpio = _get_mmio(AXI_GPIO_0_BASE)
    gpio.write(REG_GPIO2_DATA, 0x1)      # 拉高 start
    try:
        time.sleep(duration_ms / 1000.0)  # 等待指定毫秒
    finally:
        gpio.write(REG_GPIO2_DATA, 0x0)  # 拉低 start


def read_IQ():
    """读取 48 位 acc_I 和 acc_Q，返回两个 Python 有符号整数 (acc_I, acc_Q)。

    读取侧映射（需在 Block Design 接入后生效）：
        ACC_I_GPIO_BASE: channel1 = acc_I[31:0]；channel2 低 16 位 = acc_I[47:32]
        ACC_Q_GPIO_BASE: channel1 = acc_Q[31:0]；channel2 低 16 位 = acc_Q[47:32]
    """
    if ACC_I_GPIO_BASE is None or ACC_Q_GPIO_BASE is None:
        raise RuntimeError(
            "读取侧 GPIO 未配置：当前 add_axi.xsa 中 out_acc_I / out_acc_Q 悬空。"
            "请先在 Block Design 接入读取通道，并填写 ACC_I_GPIO_BASE / ACC_Q_GPIO_BASE。")

    gpio_i = _get_mmio(ACC_I_GPIO_BASE)
    gpio_q = _get_mmio(ACC_Q_GPIO_BASE)

    acc_i_raw = ((gpio_i.read(REG_GPIO2_DATA) & 0xFFFF) << 32) | gpio_i.read(REG_GPIO_DATA)
    acc_q_raw = ((gpio_q.read(REG_GPIO2_DATA) & 0xFFFF) << 32) | gpio_q.read(REG_GPIO_DATA)

    return _to_signed_48(acc_i_raw), _to_signed_48(acc_q_raw)


def measure_once(freq_hz, duration_ms=2.0):
    """便捷接口：一次完整测量并回读结果。

    流程：set_freq -> 拉高 start -> 等待 -> read_IQ（start 仍为高，结果未清零）
           -> 拉低 start。
    返回: (acc_I, acc_Q) 两个 Python 有符号整数。
    """
    set_freq(freq_hz)
    gpio = _get_mmio(AXI_GPIO_0_BASE)
    gpio.write(REG_GPIO2_DATA, 0x1)
    try:
        time.sleep(duration_ms / 1000.0)
        return read_IQ()
    finally:
        gpio.write(REG_GPIO2_DATA, 0x0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PS 侧硬件控制驱动（PYNQ-Z2）")
    parser.add_argument("--freq", type=float, default=1000.0, help="测量频率 Hz（默认 1000）")
    parser.add_argument("--duration_ms", type=float, default=2.0, help="start 保持毫秒数（默认 2）")
    args = parser.parse_args()

    phase_inc = set_freq(args.freq)
    print("phase_inc = 0x%08X (%d)" % (phase_inc, phase_inc))
    try:
        acc_i, acc_q = measure_once(args.freq, args.duration_ms)
        print("acc_I = %d" % acc_i)
        print("acc_Q = %d" % acc_q)
    except RuntimeError as err:
        print("读取侧未就绪：%s" % err)
