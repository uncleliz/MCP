#!/usr/bin/env bash
# scripts/squad/telegram-bridge.sh — hai chiều Telegram ⇄ squad (Kiro CLI).
#
# Nhận tin nhắn bạn gửi cho bot Telegram, lọc đúng TELEGRAM_CHAT_ID, chạy agent
# `squad` của Kiro CLI ở chế độ không-tương-tác trong thư mục project, rồi gửi
# kết quả về lại Telegram. Giữ ngữ cảnh giữa các tin bằng session id của Kiro
# (lưu ở <meta>/telegram.session).
#
# Cơ chế: long polling (getUpdates). Chỉ cần `curl`. Chạy nền trên chính máy có
# project. Dùng chung secrets với notify.sh: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# trong <meta>/notify.local.env.
#
# Lệnh đặc biệt gõ trên Telegram:
#   /new <đề bài>   bắt đầu một phiên squad mới (bỏ ngữ cảnh cũ)
#   /reset          quên session hiện tại (tin sau sẽ mở phiên mới)
#   /id             in chat id của bạn (tiện khi lấy TELEGRAM_CHAT_ID)
#   /ping           kiểm tra bridge còn sống
# Mọi tin khác được nối tiếp vào phiên hiện tại (hoặc mở mới nếu chưa có).
#
# Managed by opc-init — cài lại sẽ ghi đè file này.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SQ="$ROOT/.claude/squad"; [[ -d "$SQ" ]] || SQ="$ROOT/.kiro/squad"; [[ -d "$SQ" ]] || SQ="$ROOT/.opencode/squad"
CONFIG="$SQ/config.env"
SECRETS="$SQ/notify.local.env"
STATE_SESSION="$SQ/telegram.session"   # session id của Kiro để --resume-id
STATE_OFFSET="$SQ/telegram.offset"     # update_id offset của getUpdates
LOG="$SQ/telegram-bridge.log"

get() { [[ -f "$1" ]] && grep "^$2=" "$1" | head -1 | cut -d= -f2- || true; }

BOT_TOKEN="$(get "$SECRETS" TELEGRAM_BOT_TOKEN)"
CHAT_ID="$(get "$SECRETS" TELEGRAM_CHAT_ID)"
AGENT="$(get "$CONFIG" SQUAD_AGENT)"; AGENT="${AGENT:-squad}"
# Tập tool được tin tưởng khi chạy không-tương-tác. Mặc định cho phép tất cả để
# squad làm việc trọn vẹn (đọc/ghi/chạy lệnh/deploy). Thu hẹp bằng cách đặt
# TELEGRAM_TRUST_TOOLS trong config.env, ví dụ: TELEGRAM_TRUST_TOOLS=fs_read
TRUST="$(get "$CONFIG" TELEGRAM_TRUST_TOOLS)"

command -v curl >/dev/null 2>&1 || { echo "telegram-bridge: curl not found" >&2; exit 1; }
command -v kiro-cli >/dev/null 2>&1 || { echo "telegram-bridge: kiro-cli not found in PATH" >&2; exit 1; }
if [[ -z "$BOT_TOKEN" || -z "$CHAT_ID" ]]; then
  echo "telegram-bridge: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID chưa đặt trong $SECRETS" >&2
  exit 1
fi

API="https://api.telegram.org/bot$BOT_TOKEN"
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

# jq nếu có thì parse chuẩn; không có thì rơi về sed/grep (chỉ cho các field đơn giản).
HAS_JQ=0; command -v jq >/dev/null 2>&1 && HAS_JQ=1

# Gửi tin về Telegram. Telegram giới hạn 4096 ký tự/tin → cắt khúc.
tg_send() {
  local text="$1" chunk
  while [[ -n "$text" ]]; do
    chunk="${text:0:3900}"; text="${text:3900}"
    curl -fsS -m 20 -X POST "$API/sendMessage" \
      --data-urlencode "chat_id=$CHAT_ID" \
      --data-urlencode "text=$chunk" \
      --data-urlencode "disable_web_page_preview=true" >/dev/null 2>>"$LOG" || log "sendMessage lỗi"
  done
}

# Chạy squad. $1 = prompt, $2 = "new" để ép phiên mới. In kết quả ra stdout.
run_squad() {
  local prompt="$1" mode="${2:-}" sid="" out rc
  [[ "$mode" != "new" && -f "$STATE_SESSION" ]] && sid="$(cat "$STATE_SESSION" 2>/dev/null)"

  local -a args=(chat --no-interactive --agent "$AGENT")
  if [[ -n "$TRUST" ]]; then args+=(--trust-tools="$TRUST"); else args+=(--trust-all-tools); fi
  [[ -n "$sid" ]] && args+=(--resume-id "$sid")
  args+=("$prompt")

  log "run: kiro-cli ${args[*]}"
  out="$(cd "$ROOT" && kiro-cli "${args[@]}" 2>>"$LOG")"; rc=$?

  # Sau khi chạy, phiên mới nhất của cwd này chính là phiên vừa dùng/tạo → lưu lại.
  local newsid
  newsid="$(cd "$ROOT" && kiro-cli chat --list-sessions --format json 2>/dev/null \
    | ( [[ $HAS_JQ -eq 1 ]] && jq -r '.[0].sessions[0].sessionId // empty' \
        || sed -n 's/.*"sessionId":"\([^"]*\)".*/\1/p' | head -1 ))"
  [[ -n "$newsid" ]] && printf '%s' "$newsid" >"$STATE_SESSION"

  [[ $rc -ne 0 ]] && out="${out}"$'\n'"[bridge] kiro-cli thoát mã $rc — xem $LOG"
  printf '%s' "$out"
}

# Trích các cặp (update_id, chat_id, text) từ JSON getUpdates.
# Dùng jq nếu có; nếu không, parser tối giản (một message/dòng).
parse_updates() {
  if [[ $HAS_JQ -eq 1 ]]; then
    jq -r '.result[] | [(.update_id|tostring), (.message.chat.id|tostring),
           (.message.text // "")] | @tsv' 2>/dev/null
  else
    # Fallback thô: tách theo "update_id". Khuyến nghị cài jq để chắc chắn.
    tr '{' '\n' | grep '"update_id"' | while IFS= read -r line; do
      local uid cid txt
      uid="$(sed -n 's/.*"update_id":\([0-9]*\).*/\1/p' <<<"$line")"
      cid="$(sed -n 's/.*"chat":{"id":\(-\{0,1\}[0-9]*\).*/\1/p' <<<"$line")"
      txt="$(sed -n 's/.*"text":"\([^"]*\)".*/\1/p' <<<"$line")"
      [[ -n "$uid" ]] && printf '%s\t%s\t%s\n' "$uid" "$cid" "$txt"
    done
  fi
}

handle() {
  local text="$1" first rest
  first="${text%% *}"; rest="${text#"$first"}"; rest="${rest# }"
  case "$first" in
    /ping)  tg_send "pong — bridge sống, agent=$AGENT, project=$(basename "$ROOT")" ;;
    /id)    tg_send "chat id của bạn: $CHAT_ID" ;;
    /reset) rm -f "$STATE_SESSION"; tg_send "đã quên phiên hiện tại. Tin tiếp theo sẽ mở phiên mới." ;;
    /new)
      [[ -z "$rest" ]] && { tg_send "cú pháp: /new <đề bài>"; return; }
      tg_send "⏳ mở phiên squad mới…"
      tg_send "$(run_squad "$rest" new)" ;;
    /start|/help)
      tg_send $'Bridge squad ⇄ Telegram.\n/new <đề bài> — phiên mới\n(nhắn thường) — nối phiên hiện tại\n/reset — quên phiên\n/id — xem chat id\n/ping — kiểm tra' ;;
    *)
      [[ -z "$text" ]] && return
      tg_send "⏳ đang xử lý…"
      tg_send "$(run_squad "$text")" ;;
  esac
}

offset="$(cat "$STATE_OFFSET" 2>/dev/null || echo 0)"
log "bridge khởi động (agent=$AGENT, chat_id=$CHAT_ID, offset=$offset, jq=$HAS_JQ)"
[[ $HAS_JQ -eq 0 ]] && log "CẢNH BÁO: không có jq — dùng parser thô, nên cài jq cho chắc chắn."

trap 'log "bridge dừng"; kill 0 2>/dev/null; exit 0' INT TERM

# timeout poll ngắn (25s) để process phản hồi lệnh dừng nhanh; curl -m 30 > poll để không tự cắt sớm.
POLL=25
while :; do
  resp="$(curl -fsS -m $((POLL + 5)) "$API/getUpdates?timeout=$POLL&offset=$((offset + 1))" 2>>"$LOG")" || { sleep 3; continue; }
  # 409 = có một bridge khác đang getUpdates cho cùng bot. Tránh đánh nhau: thoát.
  if [[ "$resp" == *'"error_code":409'* ]]; then
    log "LỖI 409: đã có một bridge khác đang chạy cho bot này — thoát để tránh xung đột."
    tg_send "⚠️ phát hiện một bridge khác đang chạy (409). Instance này tự thoát. Hãy chạy 'telegram-bridge-ctl.sh stop' rồi 'start' lại đúng một bản."
    exit 1
  fi
  while IFS=$'\t' read -r uid cid txt; do
    [[ -z "$uid" ]] && continue
    offset="$uid"; printf '%s' "$offset" >"$STATE_OFFSET"
    if [[ "$cid" != "$CHAT_ID" ]]; then
      log "bỏ qua tin từ chat_id=$cid (chỉ nhận $CHAT_ID)"; continue
    fi
    # Khôi phục ký tự escape phổ biến từ JSON (\n, \", \\).
    txt="${txt//\\n/$'\n'}"; txt="${txt//\\\"/\"}"; txt="${txt//\\\\/\\}"
    log "nhận: $txt"
    handle "$txt"
  done < <(printf '%s' "$resp" | parse_updates)
done
