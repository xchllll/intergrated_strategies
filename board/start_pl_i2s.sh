#!/bin/bash
# Persistent PL/I2S bring-up. Run once after every board power-on:
#   sudo bash board/start_pl_i2s.sh
# Then start the dashboard normally. Do NOT reload PL from the dashboard.
set -e
cd /home/xilinx/jupyter_notebooks/project
exec env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
    board/start_pl_i2s.py "$@"
