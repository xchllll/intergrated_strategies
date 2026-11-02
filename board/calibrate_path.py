#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a short-calibration table from raw sweep data.

This module no longer loads the PL, configures the codec, or starts a sweep.
The dashboard performs the actual short sweep and passes the raw
``[(freq_hz, magnitude, phase_deg), ...]`` rounds here; this file only does:

* complex averaging,
* weak/coherence filtering,
* calibration-table filling.

No data is written to data/history.csv.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python_model"))

import calibration as cal  # noqa: E402

DEFAULT_TABLE = cal.CAL_TABLE_DEFAULT_PATH
DEFAULT_DAC_VOLUME = 0x10


def complex_average(sweeps):
    """Complex-average repeated sweeps, point by point.

    Returns ``[(freq, mag_avg, phase_avg, coherence, mag_spread_pct), ...]``.
    """
    if not sweeps or not sweeps[0]:
        raise ValueError("没有可用的扫频数据")
    n = len(sweeps[0])
    for sweep in sweeps:
        if len(sweep) != n:
            raise ValueError("各轮扫频点数不一致")

    result = []
    for k in range(n):
        freq = sweeps[0][k][0]
        re = im = 0.0
        mags = []
        for sweep in sweeps:
            _f, m, p = sweep[k]
            if abs(float(_f) - float(freq)) > 1e-6:
                raise ValueError("各轮扫频频点不一致")
            re += m * math.cos(math.radians(p))
            im += m * math.sin(math.radians(p))
            mags.append(m)
        re /= len(sweeps)
        im /= len(sweeps)
        mag_avg = math.hypot(re, im)
        phase_avg = math.degrees(math.atan2(im, re))
        mean_mag = sum(mags) / len(mags)
        coherence = mag_avg / mean_mag if mean_mag > 0 else 0.0
        spread = (statistics.pstdev(mags) / mean_mag * 100.0) if mean_mag > 0 else 999.0
        result.append((freq, mag_avg, phase_avg, coherence, spread))
    return result


def load_table(path):
    table = {}
    if not os.path.isfile(path):
        return table, {}
    cal.load_calibration_table(path)
    for row in cal.CAL_TABLE:
        if isinstance(row, dict) and "freq" in row:
            table[round(float(row["freq"]), 6)] = row
    return table, dict(cal.CAL_META or {})


def build_calibration_table(
    sweeps,
    *,
    state="direct",
    ref_amp=1.0,
    table_path=None,
    min_mag=None,
    drop_db=40.0,
    min_coherence=0.0,
    dac_volume=DEFAULT_DAC_VOLUME,
    note="",
    source="dashboard one-click short calibration",
):
    """Average raw sweeps and atomically replace the calibration table.

    ``sweeps`` is a list of rounds; each round is a list of
    ``(freq_hz, magnitude_raw, phase_deg_raw)`` tuples.
    """
    if state not in (cal.CAL_PATH_DIRECT, cal.CAL_PATH_BUFFER):
        raise ValueError("state 必须是 direct 或 buffer")
    if ref_amp <= 0:
        raise ValueError("ref_amp 必须为正数")
    if not sweeps:
        raise ValueError("至少需要一轮扫频数据")

    table_path = table_path or DEFAULT_TABLE
    min_mag = cal.MIN_VALID_MAG if min_mag is None else float(min_mag)

    averaged = complex_average(sweeps)
    cohs = [a[3] for a in averaged]
    spreads = [a[4] for a in averaged]

    cand = {}
    dropped_weak = dropped_coh = 0
    for freq, mag_avg, phase_avg, coherence, _spread in averaged:
        if not cal.phase_is_valid(mag_avg, min_mag):
            dropped_weak += 1
            continue
        if min_coherence > 0 and coherence < min_coherence:
            dropped_coh += 1
            continue
        cand[freq] = (mag_avg, phase_avg)

    if not cand:
        raise ValueError("没有可用的频点（检查 min_mag / min_coherence）")

    med = statistics.median([v[0] for v in cand.values()])
    floor = med * 10.0 ** (-drop_db / 20.0)
    points = {}
    for freq, (mag_avg, phase_avg) in cand.items():
        if mag_avg < floor:
            dropped_weak += 1
            continue
        points[freq] = (mag_avg, phase_avg, ref_amp / mag_avg)

    if not points:
        raise ValueError("所有频点都被弱信号门限丢弃")

    table, _old_meta = load_table(table_path)
    for freq in sorted(points):
        _mag, phase_avg, gain = points[freq]
        key = round(float(freq), 6)
        row = table.setdefault(key, {"freq": float(freq)})
        row[state] = cal.make_cal_entry(gain, -phase_avg)

    cal.CAL_TABLE = [table[key] for key in sorted(table)]
    cal.CAL_META = {
        "format": cal.CAL_FORMAT_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ref_amp": float(ref_amp),
        "dac_volume": dac_volume,
        "state": state,
        "repeat": len(sweeps),
        "periods": None,
        "freq_points": len(points),
        "source": source,
        "note": note,
    }
    cal.save_calibration_table(table_path)

    return {
        "table": table_path,
        "state": state,
        "repeat": len(sweeps),
        "points": len(points),
        "dropped_weak": dropped_weak,
        "dropped_coherence": dropped_coh,
        "coherence_median": statistics.median(cohs),
        "coherence_min": min(cohs),
        "spread_median_pct": statistics.median(spreads),
        "spread_max_pct": max(spreads),
    }


def main():
    parser = argparse.ArgumentParser(
        description="从原始短接扫频 JSON 生成校准表（不加载 PL，不写 history）")
    parser.add_argument("--input", required=True,
                        help="原始数据 JSON：[[(freq,mag,phase),...], ...]"
                             " 或 {\"sweeps\": [...]}")
    parser.add_argument("--state", choices=(cal.CAL_PATH_DIRECT, cal.CAL_PATH_BUFFER),
                        default=cal.CAL_PATH_DIRECT)
    parser.add_argument("--ref-amp", type=float, default=1.0)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--dac-volume", type=lambda s: int(s, 0), default=DEFAULT_DAC_VOLUME)
    parser.add_argument("--min-mag", type=float, default=cal.MIN_VALID_MAG)
    parser.add_argument("--drop-db", type=float, default=40.0)
    parser.add_argument("--min-coherence", type=float, default=0.0)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    sweeps = payload.get("sweeps") if isinstance(payload, dict) else payload
    result = build_calibration_table(
        sweeps,
        state=args.state,
        ref_amp=args.ref_amp,
        table_path=args.table,
        min_mag=args.min_mag,
        drop_db=args.drop_db,
        min_coherence=args.min_coherence,
        dac_volume=args.dac_volume,
        note=args.note,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
