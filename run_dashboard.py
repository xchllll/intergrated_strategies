#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard launcher.

启动时在主线程加载 codec/PL（pynq 的 Overlay 需要主线程 asyncio loop），
随后**自动启动一轮 240 点短接参考扫频**。扫完才允许“开始扫频测量”；
如果启动时接的不是短接，参考会失败，页面会提示先接短接再点“参考扫频”。
"""
import os
import sys
import threading

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
    print("[startup] loading codec/PL in main thread ...", flush=True)
    app_mod._ensure_rtl_design()
    print("[startup] auto short-reference sweep started; wait for phase alignment ...",
          flush=True)
    threading.Thread(target=app_mod._run_reference_bg, daemon=True).start()
    print("[startup] dashboard ready; sweep is blocked until reference is done",
          flush=True)
    app_mod.app.run(host="0.0.0.0", port=5000, debug=False, threaded=False)
