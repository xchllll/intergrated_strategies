#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard startup reference sweep (background, one round).

Run with the DUT replaced by a short.  It measures the short with the SAME
periods schedule used to build the existing direct calibration table, saves
data/calibration_table.json, and reports progress through status() so the web
page can disable the sweep button and show "正在进行相位对齐".

The dashboard itself starts immediately; this runs in a daemon thread.
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
        # codec/PL 的 Overlay 加载依赖主线程的 asyncio 事件循环；后台线程里
        # 直接调用会报 "There is no current event loop in thread"。
        # run_dashboard.py 已经在主线程调过 _ensure_rtl_design()，这里默认跳过；
        # 单独调用（design_loaded=False）时才自己加载（此时调用方应在主线程）。
        if not design_loaded:
            app_mod._ensure_rtl_design()

        freqs = app_mod._log_spaced_freqs(
            max(20.0, ps_ctrl.SAFE_MIN_FREQ_HZ), 20000.0, app_mod.SWEEP_POINTS)
        periods, _schedule_info = ps_ctrl.build_period_schedule(
            freqs, n_target=n_target, budget_s=0.0)
        with _LOCK:
            _STATE.update(total=len(freqs),
                          message="正在进行相位对齐（0/%d）..." % len(freqs))

        rows = []
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

        cal.CAL_TABLE = rows
        cal.CAL_META = {
            "format": getattr(cal, "CAL_FORMAT_VERSION", 2),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ref_amp": 1.0,
            "dac_volume": cal.DRIVER_STATE.get("dac_volume"),
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
