#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_dataset.py - 自动化数据集生成（20Hz ~ 20kHz 扫频，240 点）【口径统一版】

目标：在 PYNQ 板子上自动扫频，记录每个频点的幅值（dB，相对 1V 满量程基线）
与相位（度），带上标签保存为 CSV，供诊断系统/机器学习训练使用。

数据流（复用 ps_ctrl / calibration 封装的函数）：
    ps_ctrl.set_freq / set_window / measure_once
        -> (acc_I, acc_Q, window_n)          # window_n = 实际积分点数 N
    calibration.calc_mag_phase(acc_I, acc_Q, window_n)
        -> 幅值（V）、相位（度）             # N 与硬件积分窗长统一
    calibration.apply_calibration
        -> 校准后的真实幅值 / 相位
    输出行: freq,mag,phase,label

用法：
    1) 占位/预演（当前未上板，默认）：用模拟的 RC 低通响应生成示例 CSV
       python gen_dataset.py
    2) 上板实测（在 PYNQ 板子上运行）：
       python gen_dataset.py --real
    3) 常用参数：
       --fstart 20 --fstop 20000 --points 240 --cutoff 1000
       --periods 10 --label RC_LPF --output data/dataset_rc_lpf.csv

注意：
    * mag 列单位为 dB（20*log10(幅值 V / 1V)），phase 列单位为度。
    * --periods 为每个频点的积分窗覆盖信号周期数（默认 10，正整数），
      直接决定积分窗长 N = round(periods * SAMPLE_CLK / f) 与测量耗时；
      低频时 N 较大、单点耗时较长（20Hz、10 周期约 0.5 s）。
    * 原 --duration_ms 参数已废弃（积分时长由 periods 决定）。
    默认生成的 dataset_rc_lpf.csv 为 dry-run 示例数据，上板实测后请用
    --real 重新生成覆盖。
"""

import argparse
import csv
import math
import os
import sys

# 允许从仓库任意位置运行：把 board/ 与 python_model/ 加入导入路径
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "board"))
sys.path.insert(0, os.path.join(_ROOT, "python_model"))

import ps_ctrl          # noqa: E402
import calibration as cal  # noqa: E402

DEFAULT_LABEL = "RC_LPF"
DEFAULT_OUTPUT = os.path.join(_HERE, "dataset_rc_lpf.csv")
DEFAULT_PERIODS = 10


def log_spaced_freqs(fstart, fstop, points):
    """对数等间隔频率列表：从 fstart 到 fstop，共 points 个点。"""
    if points < 2:
        return [fstart]
    ratio = (fstop / fstart) ** (1.0 / (points - 1))
    return [fstart * (ratio ** i) for i in range(points)]


def simulate_rc_lpf(freq, fc):
    """一阶 RC 低通响应（dry-run 示例）：幅值 dB 与相位度。"""
    ratio = freq / fc
    mag_db = -10.0 * math.log10(1.0 + ratio * ratio)
    phase_deg = -math.degrees(math.atan(ratio))
    return mag_db, phase_deg


def measure_point(freq_hz, periods):
    """真实采集一个频点：设频 -> 设窗 -> 启动 -> 读 I/Q -> 还原 -> 校准。

    积分点数 N 由 ps_ctrl.measure_once() 返回并原样传给
    calibration.calc_mag_phase()，保证归一化口径与硬件积分窗一致。

    【单点异常护盾】真实扫频时，信号微弱/接触不良可能让某个频点的
    ps_ctrl.measure_once() 等不到 out_done 而抛 RuntimeError（超时），
    也可能在校准链路抛其他异常。这里整体 try-except，单点失败只记录
    占位数据 (-99.0 dB, 0.0 deg) 并继续，避免 240 点扫频被一个坏点中断。
    占位值在 CSV 中可被下游按 -99.0 过滤。
    """
    try:
        acc_i, acc_q, window_n = ps_ctrl.measure_once(freq_hz, periods=periods)
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        mag_cal, phase_cal = cal.apply_calibration(mag, phase, freq_hz)
        mag_db = 20.0 * math.log10(mag_cal) if mag_cal > 0.0 else float("nan")
        return mag_db, phase_cal
    except Exception:
        print(f"[ERROR] 频率 {freq_hz:.2f} Hz 测量超时/失败")
        return -99.0, 0.0


def main():
    parser = argparse.ArgumentParser(
        description="扫频数据集生成（20Hz~20kHz，240 点，默认 dry-run 示例）")
    parser.add_argument("--real", action="store_true",
                        help="真实采集（需在 PYNQ 板上运行）；默认生成 dry-run 模拟数据")
    parser.add_argument("--fstart", type=float, default=20.0, help="起始频率 Hz")
    parser.add_argument("--fstop", type=float, default=20000.0, help="截止频率 Hz")
    parser.add_argument("--points", type=int, default=240, help="扫频点数")
    parser.add_argument("--cutoff", type=float, default=1000.0,
                        help="dry-run 模拟 RC_LPF 的截止频率 Hz")
    parser.add_argument("--periods", type=int, default=DEFAULT_PERIODS,
                        help="每频点积分窗覆盖的信号周期数（默认 %d）" % DEFAULT_PERIODS)
    parser.add_argument("--label", default=DEFAULT_LABEL, help="数据标签")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV 输出路径")
    args = parser.parse_args()

    freqs = log_spaced_freqs(args.fstart, args.fstop, args.points)
    print("扫频计划：%d 点，%.1f Hz -> %.1f Hz（对数等间隔），标签=%s"
          % (len(freqs), freqs[0], freqs[-1], args.label))
    if not args.real:
        print("[dry-run] 生成模拟 RC_LPF（fc=%.0f Hz）示例数据，仅供格式验证；"
              "上板实测请加 --real 重新生成。" % args.cutoff)
    else:
        print("每点积分窗覆盖 %d 个信号周期（N 随频率自动计算）。" % args.periods)

    rows = []
    for i, f in enumerate(freqs):
        if args.real:
            mag_db, phase = measure_point(f, args.periods)
        else:
            mag_db, phase = simulate_rc_lpf(f, args.cutoff)
        rows.append([f, mag_db, phase, args.label])
        if i % 20 == 0:
            print("  [%4d/%d] f=%9.2f Hz  mag=%7.2f dB  phase=%7.2f deg"
                  % (i + 1, len(freqs), f, mag_db, phase))

    # 写 CSV（freq 保留 2 位小数，mag/phase 保留 2 位小数）
    with open(args.output, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["freq", "mag", "phase", "label"])
        for f, mag_db, phase, label in rows:
            writer.writerow(["%.2f" % f, "%.2f" % mag_db, "%.2f" % phase, label])

    print("完成：%d 行数据已写入 %s" % (len(rows), args.output))


if __name__ == "__main__":
    main()
