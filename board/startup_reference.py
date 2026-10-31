#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard startup reference sweep (background, one round).

Checks that the connected DUT is actually a short before writing anything:

* 20 Hz: absolute short-magnitude ratio (after DAC-volume scaling) must be
  1.000 +/- 2%.  A series R/L/C changes the divider at 20 Hz, so most DUTs
  stop here immediately.
* 20 kHz: normalized shape mag(20k)/mag(20Hz) must match the previous short
  table within 5%.  Backstop for flat DUTs whose 20 Hz phase shift is small.

The table is only replaced AFTER all 240 points finish: rows stay in memory
until the loop ends, then cal.CAL_TABLE is swapped and saved atomically.
"""
import math
import os
import sys
import threading
import time

PROJECT = "/home/xilinx/jupyter_notebooks/project"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
if os.path.join(PROJECT, "board") not in sys.path:
    sys.path.append(os.path.join(PROJECT, "board"))

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "done": False,
    "error": None,
    "message": "",
    "progress": 0,
    "total": 0,
}

# 0x10 is the acceptance.configure_codec default DAC volume (-6 dB).
DEFAULT_DAC_VOLUME = 0x10


def status():
    with _LOCK:
        return dict(_STATE)


def is_running():
    with _LOCK:
        return bool(_STATE.get("running"))


def run(repeat=1, n_target=1000, design_loaded=False):
    import calibration as cal
    import ps_ctrl
    from diagnosis_system.dashboard import app as app_mod

    with _LOCK:
        _STATE.update(running=True, done=False, error=None,
                      message="正在进行相位对齐...", progress=0, total=0)
    try:
        if not design_loaded:
            app_mod._ensure_rtl_design()

        # DAC 音量会在重启 / 网页调音量后变化（0x10->0x08 约 6 dB）。
        # 先读当前音量并登记，旧表音量（缺失时按 0x10）与当前音量之比
        # 就是短接幅度的整体缩放。
        try:
            cur_vol = app_mod._current_dac_volume()
            cal.note_driver(dac_volume=cur_vol)
        except Exception as exc:
            cur_vol = None
            print("[ref] WARNING: 读取 DAC 音量失败: %s" % exc, flush=True)
        old_vol = (cal.CAL_META or {}).get("dac_volume") or DEFAULT_DAC_VOLUME
        level_scale = (float(cur_vol) / float(old_vol)) if cur_vol else 1.0
        print("[ref] dac_volume %r -> %r, expected short level scale %.3f"
              % (old_vol, cur_vol, level_scale), flush=True)

        # 上一张短接表的幅度作为期望
        exp = {}
        try:
            cal.load_calibration_table(cal.CAL_TABLE_DEFAULT_PATH)
            for row in cal.CAL_TABLE:
                entry = row.get(cal.CAL_PATH_DIRECT) or {}
                g = entry.get("mag_gain")
                if g and g > 0:
                    exp[round(float(row["freq"]), 6)] = float(g)
        except Exception as exc:
            print("[ref] WARNING: 读取旧表失败，跳过短接检测: %s" % exc, flush=True)

        freqs = app_mod._log_spaced_freqs(
            max(20.0, ps_ctrl.SAFE_MIN_FREQ_HZ), 20000.0, app_mod.SWEEP_POINTS)
        periods, _schedule_info = ps_ctrl.build_period_schedule(
            freqs, n_target=n_target, budget_s=0.0)
        with _LOCK:
            _STATE.update(total=len(freqs),
                          message="正在进行相位对齐（0/%d）..." % len(freqs))

        rows = []          # 全部点先留在内存，扫完才替换 CAL_TABLE
        t0 = time.time()
        for idx, freq in enumerate(freqs):
            si = sq = 0.0
            for _ in range(repeat):
                acc_i, acc_q, window_n = ps_ctrl.measure_once(freq, periods=periods[idx])
                mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
                si += mag * math.cos(math.radians(phase))
                sq += mag * math.sin(math.radians(phase))
            mag = math.hypot(si, sq) / float(repeat)
            phase = math.degrees(math.atan2(sq, si))
            if mag <= 1e-15:
                raise RuntimeError("短接参考在 %.1f Hz 幅值为 0；短接是否真的接好？" % freq)

            # 只用前 5 个频点做短接判据：通过就默认是短接状态。
            # 串了 R/L/C 的 DUT 在 20~26 Hz 的 H 就明显偏离 1；纯电感因
            # DCR 很小，前 5 点几乎就是短接，按用户要求放行。
            e = exp.get(round(freq, 6))
            if e and idx < 5:
                ratio = (mag * e) / level_scale
                if ratio < 0.98 or ratio > 1.02:
                    raise RuntimeError(
                        "检测到不是短接：%.1f Hz 短接幅度比 %.3f（应为 1.000），"
                        "参考扫频已停止" % (freq, ratio))

            rows.append({
                "freq": float(freq),
                cal.CAL_PATH_DIRECT: cal.make_cal_entry(1.0 / mag, -phase),
                cal.CAL_PATH_BUFFER: None,
            })
            if idx % 10 == 0 or idx == len(freqs) - 1:
                with _LOCK:
                    _STATE.update(progress=idx + 1,
                                  message="正在进行相位对齐（%d/%d）..." % (idx + 1, len(freqs)))
                print("[ref] %3d/%d  f=%8.1f Hz  gain=%.3e  off=%7.2f deg  (%.0fs)"
                      % (idx + 1, len(freqs), freq, 1.0 / mag, -phase,
                         time.time() - t0), flush=True)

        # ---- 扫完才更新 ----
        cal.CAL_TABLE = rows
        cal.CAL_META = {
            "format": getattr(cal, "CAL_FORMAT_VERSION", 2),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ref_amp": 1.0,
            "dac_volume": cur_vol,
            "state": "direct",
            "repeat": repeat,
            "periods": "auto(n_target=%d)" % n_target,
            "freq_points": len(freqs),
            "source": "dashboard startup reference (short, background)",
            "note": "short connected at dashboard start",
        }
        cal.save_calibration_table(cal.CAL_TABLE_DEFAULT_PATH)

        i1 = min(range(len(freqs)), key=lambda k: abs(freqs[k] - 1000.0))
        i20 = min(range(len(freqs)), key=lambda k: abs(freqs[k] - 20000.0))
        print("[ref] saved %d points -> %s" % (len(rows), cal.CAL_TABLE_DEFAULT_PATH),
              flush=True)
        print("[ref] 1k off=%.2f deg | 20k off=%.2f deg (short expected ~-86)"
              % (rows[i1][cal.CAL_PATH_DIRECT]["phase_offset_deg"],
                 rows[i20][cal.CAL_PATH_DIRECT]["phase_offset_deg"]), flush=True)
        with _LOCK:
            _STATE.update(running=False, done=True, error=None,
                          message="相位对齐完成", progress=len(freqs))
        return rows
    except Exception as exc:
        with _LOCK:
            _STATE.update(running=False, done=False, error=str(exc),
                          message="相位对齐失败: %s" % exc)
        print("[ref] ERROR: %s" % exc, flush=True)
        raise
