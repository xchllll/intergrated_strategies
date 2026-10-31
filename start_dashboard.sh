#!/bin/bash
# 鍚姩渚挎惡寮忕數瀛愮數璺櫤鑳借瘖鏂郴缁熺綉椤?# PYNQ 3.1 闇€瑕?root + XILINX_XRT锛屼笖 XILINX_XRT 蹇呴』鍦?import pynq 涔嬪墠鐢熸晥
set -u
cd /home/xilinx/jupyter_notebooks/project || exit 1

# 1) 鏉€鎺夋墍鏈?Dashboard 鍙樹綋锛氳剼鏈柟寮?+ python -m 鏂瑰紡 + 鐩存帴璺?app.py
sudo pkill -f 'run_dashboard\.py' 2>/dev/null
sudo pkill -f 'diagnosis_system\.dashboard\.app' 2>/dev/null
sudo pkill -f 'diagnosis_system/dashboard/app\.py' 2>/dev/null
sleep 1

# 2) 绔彛蹇呴』鐪熺殑閲婃斁锛屽惁鍒欐柊瀹炰緥缁戝畾澶辫触銆佹祻瑙堝櫒杩樺湪杩炴棫杩涚▼
if sudo ss -ltnp 2>/dev/null | grep -q ':5000 '; then
  echo "绔彛 5000 浠嶈鍗犵敤锛屾嫆缁濆惎鍔細"
  sudo ss -ltnp | grep ':5000 '
  exit 1
fi

# 3) root + XILINX_XRT 蹇呴』鍦?import pynq 涔嬪墠鐢熸晥
sudo env XILINX_XRT=/usr START_WITH_REFERENCE="${START_WITH_REFERENCE:-1}" \
     /usr/local/share/pynq-venv/bin/python3 run_dashboard.py \
     > /tmp/dashboard.log 2>&1 < /dev/null &

sleep 5
tail -5 /tmp/dashboard.log
echo "--- 鐩戝惉 5000 鐨勮繘绋?---"
sudo ss -ltnp | grep ':5000 '

