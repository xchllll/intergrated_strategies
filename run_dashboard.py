#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard launcher.

By default (including a bare `python3 run_dashboard.py`) it runs a one-round
short reference sweep in a background thread *before* serving, so the direct
calibration table is re-anchored to this process. Set START_WITH_REFERENCE=0
to skip. The web page disables "开始扫频测量" and shows "正在进行相位对齐"
until the reference finishes.
"""
import os
import sys
import threading

os.environ.setdefault("XILINX_XRT", "/usr")

# 系统 python3 没有 pynq 时自动切到 PYNQ venv；这样 project 目录下直接
# `python3 run_dashboard.py` 也能加载 PL/参考扫频。
VENV_PYTHON = "/usr/local/share/pynq-venv/bin/python3"
if os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
    try:
        import pynq  # noqa: F401
    except Exception:
        os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])

PROJECT = "/home/xilinx/jupyter_notebooks/project"
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "board"))

from diagnosis_system.dashboard.app import app  # noqa: E402

if __name__ == "__main__":
    if os.environ.get("START_WITH_REFERENCE", "1") != "0":
        import startup_reference
        from diagnosis_system.dashboard import app as app_mod
        # Overlay/codec 必须在主线程加载（pynq 需要主线程的 asyncio loop）
        print("[startup] loading codec/PL in main thread ...", flush=True)
        app_mod._ensure_rtl_design()
        print("[startup] starting background reference sweep (short should be connected)",
              flush=True)
        threading.Thread(target=startup_reference.run,
                         kwargs={"repeat": 1, "design_loaded": True},
                         daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=False)
