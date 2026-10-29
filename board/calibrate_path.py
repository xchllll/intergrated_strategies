#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HP/MIC -> LINE IN 对录线：直连/缓冲路径真实幅相校准。

频点、积分周期、实际频率 f_actual 与网页扫频完全一致
（ps_ctrl.log_freqs / periods_for_frequency / measure_avg），
生成的 data/calibration_table.json 会被 dashboard 的同一组频点直接命中。

用法（板端 root）：
    sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
        board/calibrate_path.py --path direct --ref-amp 1.0
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "board"))
sys.path.insert(0, os.path.join(ROOT, "python_model"))

import acceptance
import calibration as cal
import ps_ctrl


def load_rows(path):
    rows = {}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            for row in json.load(fh):
                if isinstance(row, dict) and "freq" in row:
                    rows[round(float(row["freq"]), 6)] = row
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path",
                        choices=(cal.CAL_PATH_DIRECT, cal.CAL_PATH_BUFFER),
                        default=cal.CAL_PATH_DIRECT)
    parser.add_argument("--ref-amp", type=float, default=1.0)
    parser.add_argument("--points", type=int, default=ps_ctrl.SWEEP_POINTS)
    parser.add_argument("--start-hz", type=float,
                        default=ps_ctrl.SWEEP_START_HZ)
    parser.add_argument("--stop-hz", type=float,
                        default=ps_ctrl.SWEEP_STOP_HZ)
    parser.add_argument("--repeats", type=int,
                        default=ps_ctrl.DEFAULT_REPEATS)
    args = parser.parse_args()

    if args.ref_amp <= 0:
        raise SystemExit("--ref-amp 必须为正数")

    # 与网页扫频完全一致的顺序：先配 codec（base overlay 还在），再烧自定义设计
    acceptance.configure_codec()
    acceptance.load_design(acceptance.ensure_bitstream(), 1e8)

    freqs = ps_ctrl.log_freqs(args.start_hz, args.stop_hz, args.points)
    measured = []
    for freq in freqs:
        acc_i, acc_q, window_n, f_actual = ps_ctrl.measure_avg(
            freq,
            periods=ps_ctrl.periods_for_frequency(freq),
            repeats=args.repeats,
        )
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        measured.append((f_actual, mag, phase))
        print("f=%9.2f Hz  mag=%10.6g  phase=%8.2f"
              % (f_actual, mag, phase), flush=True)

    if not measured:
        raise SystemExit("没有有效频点")

    # 低频高衰减点的幅值接近噪声底，相位不可信，不写进校准表
    floor = max(1e-9, statistics.median([m for _, m, _ in measured]) * 0.01)
    rows = load_rows(cal.CAL_TABLE_DEFAULT_PATH)
    kept = 0
    for f_actual, mag, phase in measured:
        if mag < floor:
            continue
        key = round(float(f_actual), 6)
        row = rows.setdefault(key, {"freq": float(f_actual)})
        row[args.path] = {
            "mag_gain": args.ref_amp / mag,
            "phase_offset_deg": cal._wrap_180(-phase),
        }
        kept += 1

    table = [rows[k] for k in sorted(rows)]
    cal.CAL_TABLE = table
    cal.save_calibration_table(cal.CAL_TABLE_DEFAULT_PATH)
    print("写入 %s：%d 个频点（%s 路径；跳过 %d 个低幅值点）"
          % (cal.CAL_TABLE_DEFAULT_PATH, kept, args.path,
             len(measured) - kept))


if __name__ == "__main__":
    main()
