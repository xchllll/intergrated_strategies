#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect_dut.py - 采集 DUT 扫频曲线，追加到 data/real/<label>.csv

为什么走网页接口：
    /api/start_sweep 用的就是网页那条链路（自适应积分周期 + 校准表 +
    输入网络去嵌入），采集条件与标定、推理完全一致。自己另写一套测量
    很容易在口径上偏掉。

数据格式（长表，一条曲线占连续的 240 行，用 curve_id 区分）：
    curve_id,timestamp,label,source,freq,mag,phase,mag_raw,phase_raw,phase_valid
    mag / phase 是**校准后**的值（模型看到的就是这个），
    mag_raw / phase_raw 是未校准值，留着重标定用的。

用法（板端任意终端，网页服务需在跑）：
    python3 tools/collect_dut.py --label rc_lowpass --count 5

    # 自检（只有短接线、曲线很平的时候用）
    python3 tools/collect_dut.py --label _smoke --count 1 --allow-flat
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DIR = os.path.join(ROOT, "data", "real")

FIELDS = ["curve_id", "timestamp", "label", "source",
          "freq", "mag", "phase", "mag_raw", "phase_raw", "phase_valid"]

CLASSES = ["rc_lowpass", "rc_highpass", "rl_lowpass",
           "rl_highpass", "rlc_bandpass", "opamp_lowpass"]


def sweep_once(host, port, cal_path, timeout):
    url = "http://%s:%d/api/start_sweep" % (host, port)
    body = json.dumps({"mode": "rtl", "cal_path": cal_path}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as fh:
        return json.loads(fh.read().decode("utf-8"))


def validate(payload, min_valid, min_range, allow_flat):
    """返回 (ok, reason)。不合格的采集直接丢弃，不写进数据集。"""
    if payload.get("status") != "success":
        return False, "后端返回失败：%s" % payload.get("msg")

    data = payload.get("data") or {}
    freqs = data.get("freqs") or []
    mags = data.get("mag_db") or []
    phases = data.get("phase_deg") or []
    valid = data.get("phase_valid") or [1] * len(freqs)

    if not (len(freqs) == len(mags) == len(phases)):
        return False, "数据长度不一致：%d/%d/%d" % (len(freqs), len(mags), len(phases))
    if len(freqs) != 240:
        return False, "频点数不是 240（实际 %d），检查网页端是否改了扫频点数" % len(freqs)

    ratio = sum(1 for v in valid if v) / float(len(freqs))
    if ratio < min_valid:
        return False, "有效频点比例 %.1f%% < %.0f%%" % (ratio * 100, min_valid * 100)

    span = max(mags) - min(mags)
    if span < min_range and not allow_flat:
        return False, ("动态范围只有 %.2f dB（< %.1f），八成没接电路或短路；"
                       "确实要采这种数据请加 --allow-flat" % (span, min_range))
    return True, "ok (valid %.0f%%, span %.2f dB)" % (ratio * 100, span)


def count_curves(path):
    if not os.path.isfile(path):
        return 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return len({row["curve_id"] for row in csv.DictReader(fh)})


def append_curve(path, curve_id, label, payload):
    data = payload["data"]
    freqs = data["freqs"]
    mags = data["mag_db"]
    phases = data["phase_deg"]
    raw_mag = data.get("mag_v_raw") or [0.0] * len(freqs)
    raw_phase = data.get("phase_deg_raw") or [0.0] * len(freqs)
    valid = data.get("phase_valid") or [1] * len(freqs)
    source = (payload.get("source") or {}).get("mode", "unknown")
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")

    new_file = not os.path.isfile(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(FIELDS)
        for index, freq in enumerate(freqs):
            writer.writerow([
                curve_id, stamp, label, source, "%.6f" % float(freq),
                "%.4f" % float(mags[index]), "%.6f" % float(phases[index]),
                "%.9g" % float(raw_mag[index]), "%.6f" % float(raw_phase[index]),
                int(bool(valid[index])) if index < len(valid) else 1,
            ])


def main():
    parser = argparse.ArgumentParser(
        description="采集 DUT 扫频数据并追加到 data/real/<label>.csv")
    parser.add_argument("--label", required=True,
                        help="电路标签，建议用 %s 之一" % "/".join(CLASSES))
    parser.add_argument("--count", type=int, default=1, help="采集几条曲线（默认 1）")
    parser.add_argument("--cal-path", choices=("direct", "buffer"), default="direct",
                        help="校准路径（默认 direct）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=180.0, help="单次请求超时秒数")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="两次采集之间等待秒数（散热/稳定）")
    parser.add_argument("--min-valid", type=float, default=0.95,
                        help="有效频点比例下限（默认 0.95）")
    parser.add_argument("--min-range", type=float, default=3.0,
                        help="幅值动态范围下限 dB（默认 3.0）")
    parser.add_argument("--allow-flat", action="store_true",
                        help="允许很平的曲线（自检用）")
    parser.add_argument("--out-dir", default=REAL_DIR, help="输出目录")
    args = parser.parse_args()

    if args.label not in CLASSES and not args.label.startswith("_"):
        print("[提示] 标签 %r 不在标准六类里，训练时会被当成额外类别" % args.label)

    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, "%s.csv" % args.label)
    done = 0

    for index in range(args.count):
        tag = "[%d/%d]" % (index + 1, args.count)
        started = time.monotonic()
        try:
            payload = sweep_once(args.host, args.port, args.cal_path, args.timeout)
        except Exception as exc:                        # noqa: BLE001
            print("%s 采集失败：%r" % (tag, exc))
            break

        ok, reason = validate(payload, args.min_valid, args.min_range, args.allow_flat)
        if not ok:
            print("%s 丢弃：%s" % (tag, reason))
            continue

        n = count_curves(path) + 1
        curve_id = "%s_%04d" % (args.label, n)
        append_curve(path, curve_id, args.label, payload)
        done += 1
        print("%s %s 已写入 %s（%s，用时 %.1fs）"
              % (tag, curve_id, os.path.basename(path), reason,
                 time.monotonic() - started))
        if index + 1 < args.count:
            time.sleep(args.interval)

    print("\n完成：本次新增 %d 条，累计 %d 条 -> %s"
          % (done, count_curves(path), path))
    return 0 if done or args.count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
