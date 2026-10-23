#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_dataset.py - 自动化数据集生成（20Hz ~ 20kHz 扫频，240 点）

目标：在 PYNQ 板子上自动扫频，记录每个频点的幅值（dB，相对 1V 满量程基线）
与相位（度），带上标签保存为 CSV，供诊断系统/机器学习训练使用。

数据流（复用前两步封装的函数）：
    ps_ctrl.set_freq / start_measure / read_IQ
        -> 48 位 acc_I / acc_Q
    calibration.calc_mag_phase
        -> 幅值（V）、相位（度）
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
       --duration_ms 2.0 --label RC_LPF --output data/dataset_rc_lpf.csv

注意：mag 列单位为 dB（20*log10(幅值 V / 1V)），phase 列单位为度。
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


def measure_point(freq_hz, duration_ms):
    """真实采集一个频点：设频 -> 启动测量 -> 读 I/Q -> 还原 -> 校准。"""
    acc_i, acc_q = ps_ctrl.measure_once(freq_hz, duration_ms)
    mag, phase = cal.calc_mag_phase(acc_i, acc_q)
    mag_cal, phase_cal = cal.apply_calibration(mag, phase, freq_hz)
    mag_db = 20.0 * math.log10(mag_cal) if mag_cal > 0.0 else float("nan")
    return mag_db, phase_cal


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
    parser.add_argument("--duration_ms", type=float, default=2.0,
                        help="每个频点 start 保持毫秒数（真实采集用）")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="数据标签")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="CSV 输出路径")
    args = parser.parse_args()

    freqs = log_spaced_freqs(args.fstart, args.fstop, args.points)
    print("扫频计划：%d 点，%.1f Hz -> %.1f Hz（对数等间隔），标签=%s"
          % (len(freqs), freqs[0], freqs[-1], args.label))
    if not args.real:
        print("[dry-run] 生成模拟 RC_LPF（fc=%.0f Hz）示例数据，仅供格式验证；"
              "上板实测请加 --real 重新生成。" % args.cutoff)

    rows = []
    for i, f in enumerate(freqs):
        if args.real:
            mag_db, phase = measure_point(f, args.duration_ms)
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
