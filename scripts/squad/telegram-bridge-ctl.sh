#!/usr/bin/env bash
# scripts/squad/telegram-bridge-ctl.sh {start|stop|status|logs|foreground}
# Quản lý vòng đời bridge Telegram ⇄ squad. Chạy nền bằng nohup, lưu PID để dừng.
# Managed by opc-init — cài lại sẽ ghi đè file này.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SQ="$ROOT/.claude/squad"; [[ -d "$SQ" ]] || SQ="$ROOT/.kiro/squad"; [[ -d "$SQ" ]] || SQ="$ROOT/.opencode/squad"
BRIDGE="$(dirname "$0")/telegram-bridge.sh"
PIDFILE="$SQ/telegram-bridge.pid"
LOG="$SQ/telegram-bridge.log"

alive() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
# PID bridge của project này. Chỉ lấy tiến trình cha (bỏ subshell con < <(...)).
running_pids() {
  local p ppid
  for p in $(pgrep -f "bash $BRIDGE" 2>/dev/null); do
    ppid="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
    # cha của subshell cũng là bridge -> bỏ con, chỉ giữ tiến trình gốc
    pgrep -f "bash $BRIDGE" 2>/dev/null | grep -qx "$ppid" || echo "$p"
  done
}

case "${1:-}" in
  start)
    # Chống chạy trùng: Telegram chỉ cho một getUpdates/bot, nhiều instance -> lỗi 409.
    existing="$(running_pids)"
    if [[ -n "$existing" ]]; then
      echo "bridge đã chạy (pid $(echo "$existing" | tr '\n' ' ')) — không khởi động thêm" >&2; exit 0
    fi
    nohup bash "$BRIDGE" >>"$LOG" 2>&1 &
    echo $! >"$PIDFILE"
    sleep 2
    if alive; then echo "bridge khởi động (pid $(cat "$PIDFILE")). Log: $LOG"
    else echo "bridge không khởi động được — xem $LOG" >&2; tail -n 20 "$LOG" 2>/dev/null; exit 1; fi ;;
  stop)
    pids="$(running_pids)"
    if [[ -n "$pids" ]]; then
      # TERM trước (để trap ghi log), chờ ngắn, rồi KILL cả cây process (curl kẹt m60).
      kill $pids 2>/dev/null; sleep 2
      # Bao gồm cả subshell con: kill mọi tiến trình khớp script này.
      remaining="$(pgrep -f "bash $BRIDGE" 2>/dev/null)"
      [[ -n "$remaining" ]] && kill -9 $remaining 2>/dev/null
      sleep 1
      echo "đã dừng bridge"
    else echo "bridge không chạy"; fi
    rm -f "$PIDFILE" ;;
  status)
    pids="$(running_pids)"
    if [[ -n "$pids" ]]; then echo "đang chạy (pid $(echo "$pids" | tr '\n' ' '))"; else echo "không chạy"; fi ;;
  logs)    touch "$LOG"; tail -n "${2:-40}" -f "$LOG" ;;
  foreground) exec bash "$BRIDGE" ;;   # chạy trực tiếp để debug (Ctrl-C để dừng)
  *) echo "dùng: $0 {start|stop|status|logs [N]|foreground}" >&2; exit 2 ;;
esac
