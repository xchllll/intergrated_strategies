#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard launcher.

START_WITH_REFERENCE=1 (or start_dashboard_ref.sh) starts a one-round short
reference sweep in a background thread while the web server comes up
immediately.  The web page disables "开始扫频测量" and shows
"正在进行相位对齐" until the reference finishes.
"""
import os
import sys
import threading

os.environ.setdefault("XILINX_XRT", "/usr")

PROJECT = "/home/xilinx/jupyter_notebooks/project"
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "board"))

from diagnosis_system.dashboard.app import app  # noqa: E402

if __name__ == "__main__":
    if os.environ.get("START_WITH_REFERENCE") == "1":
        import startup_reference
        from diagnosis_system.dashboard import app as app_mod
        # Overlay/codec 必须在主线程加载（pynq 需要主线程的 asyncio loop）。
        # 先花几秒加载，再起后台参考线程，网页随后立即起来。
        print("[startup] loading codec/PL in main thread ...", flush=True)
        app_mod._ensure_rtl_design()
        print("[startup] starting background reference sweep (short should be connected)",
              flush=True)
        threading.Thread(target=startup_reference.run,
                         kwargs={"repeat": 1, "design_loaded": True},
                         daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=False)
