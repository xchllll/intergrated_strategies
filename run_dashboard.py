#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard launcher (persistent-PL mode).

The PL/I2S/Codec bring-up is done once after each board power-on by:

    sudo bash board/start_pl_i2s.sh

This launcher only starts Flask.  It deliberately does not load the overlay,
configure the codec, or touch the I2S stream; dashboard restarts therefore
leave the Codec frame state untouched.
"""
import os
import sys

os.environ.setdefault("XILINX_XRT", "/usr")

# 系统 python3 没有 pynq 时自动切到 PYNQ venv；这样 project 目录下直接
# `python3 run_dashboard.py` 也能加载 PL。
VENV_PYTHON = "/usr/local/share/pynq-venv/bin/python3"
if os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
    try:
        import pynq  # noqa: F401
    except Exception:
        os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])

PROJECT = "/home/xilinx/jupyter_notebooks/project"
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "board"))

from diagnosis_system.dashboard import app as app_mod  # noqa: E402

if __name__ == "__main__":
    print("[startup] dashboard only; PL/I2S must already be running", flush=True)
    if not app_mod._persistent_pl_ready():
        print("[startup] WARNING: /tmp/pl_i2s_ready.json missing or stale;",
              flush=True)
        print("[startup] run: sudo bash board/start_pl_i2s.sh", flush=True)
    else:
        # PYNQ's MMIO/device discovery must be initialized in the main thread.
        # The short-calibration worker runs in a background thread; without
        # this, its first MMIO access raises "There is no current event loop".
        try:
            import ps_ctrl
            ps_ctrl.read_done()
            print("[startup] AXI GPIO/MMIO ready", flush=True)
        except Exception as exc:
            print("[startup] WARNING: AXI GPIO init failed: %s" % exc,
                  flush=True)
    app_mod.app.run(host="0.0.0.0", port=5000, debug=False, threaded=False)
