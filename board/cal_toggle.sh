#!/usr/bin/env bash
# 校准表开关（热生效，无需重启 dashboard）
# 用法: bash board/cal_toggle.sh {direct|buffer|off|all|status}
#   direct : 直连表 + H 平面表开，缓冲表关
#   buffer : 缓冲表开，直连表 + H 平面表关（缓冲校准测试用）
#   off    : 全关，返回原始测量
#   all    : 全开（网页按 cal_path=direct/buffer 选择）
#   status : 打印当前开关
set -e
cd "$(dirname "$0")/.."
MODE=data/cal_mode.json

write_mode() {
  printf '%s\n' "$1" > "$MODE.tmp"
  mv "$MODE.tmp" "$MODE"
  echo "cal_mode -> $1"
}

case "${1:-status}" in
  direct) write_mode '{"direct": true, "buffer": false, "hplane": true}' ;;
  buffer) write_mode '{"direct": false, "buffer": true, "hplane": false}' ;;
  off)    write_mode '{"direct": false, "buffer": false, "hplane": false}' ;;
  all)    write_mode '{"direct": true, "buffer": true, "hplane": true}' ;;
  status) echo -n "cal_mode: "; cat "$MODE" 2>/dev/null || echo "（未设置：全部启用）" ;;
  *) echo "用法: bash board/cal_toggle.sh {direct|buffer|off|all|status}"; exit 1 ;;
esac
