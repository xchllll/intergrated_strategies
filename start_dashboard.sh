#!/bin/bash
# 启动便携式电子电路智能诊断系统网页（PYNQ 3.1 需要 root + XILINX_XRT）
#
#   ./start_dashboard.sh            启动，并后台做一次短接参考标定
#                                   （此刻必须接【短接线】，不能接电路！）
#   ./start_dashboard.sh --no-ref   启动但不重标，保留现有校准表
#
cd /home/xilinx/jupyter_notebooks/project || exit 1

# 整体以 root 重新执行一次，避免每条 sudo 都要输密码
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

pkill -f 'run_dashboard' 2>/dev/null
sleep 1

if [ "$1" = "--no-ref" ]; then
    export START_WITH_REFERENCE=0
    echo "[start] 跳过启动参考扫频（保留现有校准表）"
else
    export START_WITH_REFERENCE=1
    echo "[start] 将执行启动参考扫频 —— 请确认此刻接的是【短接线】！"
    echo "        已接电路请 Ctrl+C，改用 ./start_dashboard.sh --no-ref"
    sleep 3
fi

# setsid + stdin 重定向：彻底脱离终端，不会再卡住光标
setsid env XILINX_XRT=/usr START_WITH_REFERENCE="${START_WITH_REFERENCE}" \
    /usr/local/share/pynq-venv/bin/python3 run_dashboard.py \
    > /tmp/dashboard.log 2>&1 < /dev/null &
disown 2>/dev/null
sleep 4
tail -3 /tmp/dashboard.log
echo "打开 http://$(hostname -I | awk '{print $1}'):5000"
