#!/bin/bash
# 启动便携式电子电路智能诊断系统网页（PYNQ 3.1 需要 root + XILINX_XRT）
# 参考扫频不再随启动自动执行：dashboard 起来后，在网页右下角点“参考扫频”。
set -u
cd /home/xilinx/jupyter_notebooks/project || exit 1

# 1) 杀掉所有 Dashboard 变体：脚本方式 + python -m + 直接跑 app.py
sudo pkill -f 'run_dashboard\.py' 2>/dev/null
sudo pkill -f 'diagnosis_system\.dashboard\.app' 2>/dev/null
sudo pkill -f 'diagnosis_system/dashboard/app\.py' 2>/dev/null
sleep 1

# 2) 端口必须真的释放，否则新实例绑定失败、浏览器还在连旧进程
if sudo ss -ltnp 2>/dev/null | grep -q ':5000 '; then
  echo "端口 5000 仍被占用，拒绝启动："
  sudo ss -ltnp | grep ':5000 '
  exit 1
fi

# 3) root + XILINX_XRT 必须在 import pynq 之前生效
sudo env XILINX_XRT=/usr \
     /usr/local/share/pynq-venv/bin/python3 run_dashboard.py \
     > /tmp/dashboard.log 2>&1 < /dev/null &

sleep 5
tail -5 /tmp/dashboard.log
echo "--- 监听 5000 的进程 ---"
sudo ss -ltnp | grep ':5000 '
