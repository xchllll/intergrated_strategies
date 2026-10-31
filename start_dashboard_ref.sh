#!/bin/bash
set -u
cd /home/xilinx/jupyter_notebooks/project || exit 1
export START_WITH_REFERENCE=1
bash start_dashboard.sh
for i in $(seq 1 40); do
  if curl -s -m 2 "http://127.0.0.1:5000/api/status?cal_path=direct" >/dev/null 2>&1; then
    echo "dashboard up; reference alignment runs in background (sweep button disabled until done)"
    exit 0
  fi
  sleep 3
done
echo "dashboard did not come up; last log:"
tail -30 /tmp/dashboard.log 2>/dev/null
exit 1

