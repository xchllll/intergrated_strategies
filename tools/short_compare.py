#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""short_compare.py - 对比多张短接校准表的帧偏移与残差（离线工具）。

用法：
    python3 tools/short_compare.py \
      --reference data/calibration_table.json \
      --cal data/calibration_table.iter1.json \
            data/calibration_table.iter2.json \
            data/calibration_table.iter3.json

输出：
  * 每张表相对 reference 的原始残差；
  * 估计的整数采样偏移 m_int 与分数偏移 m_frac；
  * 用 m_int / m_frac 对齐后的残差；
  * 两两对比矩阵。

该工具不修改任何校准表，也不参与 dashboard 的实时测量路径；用于验证
“不同 session 的短接表差几个采样”以及 RTL 启动时序是否把偏移压到 0。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python_model"))

import short_alignment as SA  # noqa: E402


def _label(path: str, index: int, used: dict) -> str:
    stem = Path(path).stem
    if stem not in used:
        used[stem] = 1
        return stem
    used[stem] += 1
    return "%s_%d" % (stem, used[stem])


def _load_pair(path: str, ref_path: str, fs: float):
    f_ref, c_ref, meta_ref = SA.load_short_table(ref_path)
    f_new, c_new, meta_new = SA.load_short_table(path)
    c_new_on_ref = SA.interp_complex_log(f_ref, f_new, c_new)
    return f_ref, c_new_on_ref, c_ref, meta_new, meta_ref


def _fmt(x: float, digits: int = 3) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return ("%." + str(digits) + "f") % float(x)


def _write_aligned_table(
    path: str,
    f: np.ndarray,
    coeff: np.ndarray,
    meta: dict,
    m: float,
    fs: float,
    output_dir: str,
) -> str:
    """把对齐后的短接表写成 format 2 JSON，供人工检查/后续比较。"""
    os.makedirs(output_dir, exist_ok=True)
    c = coeff * SA.delay_factor(f, m, fs)
    rows = []
    for freq, cc in zip(f, c):
        gain = abs(cc)
        off = math.degrees(math.atan2(cc.imag, cc.real))
        off = ((off + 180.0) % 360.0) - 180.0
        rows.append(
            {
                "freq": float(freq),
                "direct": {
                    "mag_gain": float(gain),
                    "phase_offset_deg": float(off),
                    "h_re": float(cc.real),
                    "h_im": float(cc.imag),
                },
                "buffer": None,
            }
        )
    payload = {
        "format": 2,
        "meta": {
            "created": meta.get("created"),
            "source": "tools/short_compare.py alignment",
            "frame_offset_samples": float(m),
            "alignment_reference": os.path.basename(path),
            "freq_points": len(rows),
        },
        "rows": rows,
    }
    out = os.path.join(output_dir, Path(path).stem + ".aligned.json")
    Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001  Python <3.7 或特殊终端
        pass
    parser = argparse.ArgumentParser(
        description="对比多张短接校准表的帧偏移与残差",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 tools/short_compare.py \\\n"
            "    --reference cal_board.json \\\n"
            "    --cal cal_iter1.json cal_iter2.json cal_iter3.json \\\n"
            "    --output /tmp/short_compare.md"
        ),
    )
    parser.add_argument("--cal", nargs="+", required=True, help="待比较的短接表 JSON（至少 2 张）")
    parser.add_argument("--reference", default=None, help="参考短接表；缺省用 --cal 的第一张")
    parser.add_argument("--fs", type=float, default=SA.DEFAULT_FS, help="采样率 Hz（默认 48828.125）")
    parser.add_argument("--max-samples", type=int, default=3, help="整数偏移搜索范围 ±N（默认 3）")
    parser.add_argument("--output", default=None, help="可选：写入 Markdown 报告")
    parser.add_argument("--csv", default=None, help="可选：写入 CSV")
    parser.add_argument("--json", default=None, help="可选：写入 JSON")
    parser.add_argument(
        "--corrected-dir",
        default=None,
        help="可选：把每张表按估计偏移对齐后写入该目录",
    )
    parser.add_argument(
        "--corrected-mode",
        choices=("int", "frac"),
        default="int",
        help="对齐使用 int 或 frac 偏移（默认 int）",
    )
    args = parser.parse_args(argv)

    if args.max_samples < 0:
        parser.error("--max-samples 必须 >= 0")
    if args.fs <= 0:
        parser.error("--fs 必须为正数")
    if len(args.cal) < 2:
        parser.error("至少给两张 --cal")

    ref_path = args.reference or args.cal[0]
    labels = {}
    names = {}
    for index, path in enumerate(args.cal):
        names[path] = _label(path, index, labels)

    rows = []
    pairs = []
    loaded = {}
    aligned_paths = []
    f_ref = c_ref = None
    for path in args.cal:
        if not os.path.isfile(path):
            print("ERROR: 找不到 %s" % path, file=sys.stderr)
            return 2
        f, c_on_ref, c_ref_here, meta, meta_ref = _load_pair(path, ref_path, args.fs)
        if f_ref is None:
            f_ref, c_ref = f, c_ref_here
        loaded[path] = (f, c_on_ref, meta)
        result = SA.estimate_offset(
            f, c_on_ref, c_ref_here, fs=args.fs, max_samples=args.max_samples
        )
        result.update({"label": names[path], "path": path, "created": meta.get("created")})
        if args.corrected_dir:
            m_use = result["m_int"] if args.corrected_mode == "int" else result["m_frac"]
            aligned_paths.append(
                _write_aligned_table(
                    path, f, c_on_ref, meta, float(m_use), args.fs, args.corrected_dir
                )
            )
        rows.append(result)

    # 两两矩阵
    paths = list(args.cal)
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            f_i, c_i, _ = loaded[paths[i]]
            f_j, c_j, _ = loaded[paths[j]]
            # 以 i 为参考，把 j 插值到 i 的频点（两者通常同网格）
            c_j_on_i = SA.interp_complex_log(f_i, f_j, c_j)
            res = SA.estimate_offset(
                f_i, c_j_on_i, c_i, fs=args.fs, max_samples=args.max_samples
            )
            pairs.append(
                {
                    "a": names[paths[i]],
                    "b": names[paths[j]],
                    "m_int": res["m_int"],
                    "m_frac": res["m_frac"],
                    "raw_db_rms": res["raw_db_rms"],
                    "raw_phase_rms_deg": res["raw_phase_rms_deg"],
                    "int_db_rms": res["int_db_rms"],
                    "int_phase_rms_deg": res["int_phase_rms_deg"],
                    "frac_db_rms": res["frac_db_rms"],
                    "frac_phase_rms_deg": res["frac_phase_rms_deg"],
                }
            )

    lines = []
    lines.append("# 短接表对齐对比")
    lines.append("")
    lines.append("- 参考表：`%s`（%s）" % (ref_path, (SA.load_short_table(ref_path)[2] or {}).get("created", "?")))
    lines.append("- 采样率：`%.6f Hz`，一个采样 `%.5f µs`" % (args.fs, 1e6 / args.fs))
    lines.append("- 偏移约定：`S_new(f) * exp(+j2π f m/fs) ≈ S_ref(f)`")
    lines.append("")
    lines.append("## 相对参考表的残差")
    lines.append("")
    lines.append("| 表 | created | m_int | m_frac | 原始 dB rms | 原始相位 rms | int 校正 dB rms | int 校正相位 rms | frac 校正 dB rms | frac 校正相位 rms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        lines.append(
            "| `%s` | %s | %d | %s | %s | %s | %s | %s | %s | %s |"
            % (
                row["label"],
                row.get("created") or "?",
                row["m_int"],
                _fmt(row["m_frac"], 4),
                _fmt(row["raw_db_rms"]),
                _fmt(row["raw_phase_rms_deg"]),
                _fmt(row["int_db_rms"]),
                _fmt(row["int_phase_rms_deg"]),
                _fmt(row["frac_db_rms"]),
                _fmt(row["frac_phase_rms_deg"]),
            )
        )
    lines.append("")
    lines.append("## 两两对比")
    lines.append("")
    lines.append("| A | B | m_int | m_frac | 原始 dB rms | 原始相位 rms | int 校正相位 rms |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for pair in pairs:
        lines.append(
            "| `%s` | `%s` | %d | %s | %s | %s | %s |"
            % (
                pair["a"],
                pair["b"],
                pair["m_int"],
                _fmt(pair["m_frac"], 4),
                _fmt(pair["raw_db_rms"]),
                _fmt(pair["raw_phase_rms_deg"]),
                _fmt(pair["int_phase_rms_deg"]),
            )
        )
    lines.append("")
    lines.append("> `m_int=0` 且残差很小，说明两张表基本同状态；`m_int≠0` 且 int 校正后残差显著下降，说明差异主要是整数采样延迟；若校正后幅度/相位仍大，说明不是纯延迟（Codec/模拟状态变了），不能靠延迟校正修复。")

    text = "\n".join(lines) + "\n"
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print("saved %s" % args.output)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "label", "created", "m_int", "m_frac",
                "raw_db_rms", "raw_db_max", "raw_phase_rms_deg", "raw_phase_max_deg",
                "int_db_rms", "int_phase_rms_deg", "frac_db_rms", "frac_phase_rms_deg",
            ])
            for row in rows:
                writer.writerow([
                    row["label"], row.get("created"), row["m_int"], row["m_frac"],
                    row["raw_db_rms"], row["raw_db_max"], row["raw_phase_rms_deg"],
                    row["raw_phase_max_deg"], row["int_db_rms"], row["int_phase_rms_deg"],
                    row["frac_db_rms"], row["frac_phase_rms_deg"],
                ])
        print("saved %s" % args.csv)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"reference": ref_path, "rows": rows, "pairs": pairs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("saved %s" % args.json)
    for path in aligned_paths:
        print("saved %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
