#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用网页扫频写入的 data/history.csv 填写校准表。

工作流（在板端终端执行）：

  1) 网页点击“开始扫频测量”（每次都会把同一份数据追加到 data/history.csv）
  2) 按当前夹具状态填写校准表，例如直连态：
         python3 board/calibrate_path.py --state direct --ref-amp 1.0
     缓冲态：
         python3 board/calibrate_path.py --state buffer --ref-amp 1.0
  3) 填完某一态后手动清除历史，准备下一态：
         python3 board/calibrate_path.py --clear-history

说明：
  * history.csv 里只存未校准数据：优先使用 mag_v_raw / phase_deg_raw；
    缺少原始列的行会被跳过，绝不使用可能已校准的 mag_db / phase_deg。
  * --sweep latest（默认）只取最近一次网页扫频；--sweep all 取全部历史。
  * --mode 默认只取 pynq_rtl 的历史行（audio/demo 不会混入）；any 不过滤。
  * phase_valid=0 的低信噪比点会被跳过。
  * 只读写文件，不加载 PL、不需要 XILINX_XRT；可以直接在普通终端运行。
"""
import argparse
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python_model"))

import calibration as cal  # noqa: E402

DEFAULT_HISTORY = os.path.join(ROOT, "data", "history.csv")
DEFAULT_TABLE = cal.CAL_TABLE_DEFAULT_PATH


def _wrap180(deg):
    deg = math.fmod(deg, 360.0)
    if deg > 180.0:
        deg -= 360.0
    elif deg <= -180.0:
        deg += 360.0
    return deg


def _load_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _is_valid(row):
    value = str(row.get("phase_valid", "1")).strip().lower()
    return value not in ("0", "false", "no", "")


def _raw_point(row):
    """返回 (freq_hz, mag_raw, phase_raw_deg)；只接受未校准列，缺列返回 None。"""
    try:
        freq = float(row["freq_hz"])
        if row.get("mag_v_raw") not in (None, ""):
            mag = float(row["mag_v_raw"])
        elif row.get("mag_db_raw") not in (None, ""):
            mag = 10.0 ** (float(row["mag_db_raw"]) / 20.0)
        else:
            return None
        if row.get("phase_deg_raw") not in (None, ""):
            phase = float(row["phase_deg_raw"])
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if freq <= 0 or mag <= 0:
        return None
    return freq, mag, phase


def _load_table(path):
    table = {}
    if not os.path.isfile(path):
        return table
    try:
        cal.load_calibration_table(path)
    except Exception as exc:  # noqa: BLE001
        print("[WARN] 现有校准表读取失败，将新建：%s" % exc)
        return table
    for row in cal.CAL_TABLE:
        if isinstance(row, dict) and "freq" in row:
            table[round(float(row["freq"]), 6)] = row
    return table


def main():
    parser = argparse.ArgumentParser(
        description="用 data/history.csv 填写校准表 / 清除历史")
    parser.add_argument("--state", choices=(cal.CAL_PATH_DIRECT,
                                            cal.CAL_PATH_BUFFER),
                        help="本次历史数据对应的校准态：direct 或 buffer")
    parser.add_argument("--ref-amp", type=float, default=1.0,
                        help="DUT 输入端已知峰值幅度（V），默认 1.0 做相对校准")
    parser.add_argument("--history", default=DEFAULT_HISTORY,
                        help="历史数据 CSV（默认 data/history.csv）")
    parser.add_argument("--table", default=DEFAULT_TABLE,
                        help="校准表 JSON（默认 data/calibration_table.json）")
    parser.add_argument("--sweep", choices=("latest", "all"), default="latest",
                        help="latest=只取最近一次网页扫频（默认）；all=全部历史")
    parser.add_argument("--mode", default="pynq_rtl",
                        help="只使用该 mode 的历史行（默认 pynq_rtl；any=不过滤）")
    parser.add_argument("--clear-history", action="store_true",
                        help="删除历史数据文件后退出（填完某一态后手动执行）")
    args = parser.parse_args()

    if args.clear_history:
        if os.path.isfile(args.history):
            os.remove(args.history)
            print("已清除历史数据：%s" % args.history)
        else:
            print("历史文件不存在，无需清除：%s" % args.history)
        return 0

    if not args.state:
        parser.error("--state 必填（direct 或 buffer）；"
                     "清除历史请使用 --clear-history")
    if args.ref_amp <= 0:
        parser.error("--ref-amp 必须为正数")
    if not os.path.isfile(args.history):
        raise SystemExit("找不到历史数据：%s" % args.history)

    rows = _load_rows(args.history)
    if not rows:
        raise SystemExit("历史数据为空：%s" % args.history)

    if args.mode != "any":
        rows = [row for row in rows if row.get("mode", "") == args.mode]
        if not rows:
            raise SystemExit(
                "历史中没有 mode=%s 的数据（可用 --mode any 查看全部）"
                % args.mode)

    if args.sweep == "latest":
        latest = max((row.get("sweep_id", "") for row in rows), default="")
        rows = [row for row in rows if row.get("sweep_id", "") == latest]
        print("使用最近一次扫频：sweep_id=%s（%d 行）" % (latest, len(rows)))

    points = {}
    skipped = 0
    for row in rows:
        if not _is_valid(row):
            skipped += 1
            continue
        parsed = _raw_point(row)
        if parsed is None:
            skipped += 1
            continue
        freq, mag, phase = parsed
        points[freq] = (mag, phase)

    if not points:
        raise SystemExit("没有可用的频点（检查 phase_valid / mag_v_raw）")

    table = _load_table(args.table)
    for freq in sorted(points):
        mag, phase = points[freq]
        key = round(float(freq), 6)
        row = table.setdefault(key, {"freq": float(freq)})
        row[args.state] = {
            "mag_gain": args.ref_amp / mag,
            "phase_offset_deg": _wrap180(-phase),
        }

    cal.CAL_TABLE = [table[key] for key in sorted(table)]
    cal.save_calibration_table(args.table)
    print("已写入 %s：%s 路径 %d 个频点（跳过 %d 个无效点；参考幅值 %.4g）"
          % (args.table, args.state, len(points), skipped, args.ref_amp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
