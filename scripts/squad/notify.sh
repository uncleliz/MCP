#!/usr/bin/env bash
# scripts/squad/notify.sh <event> "<message>"
# Fan-out notification for the squad. Channels come from NOTIFY_CHANNELS in .claude/squad/config.env;
# secrets (webhook URLs, e-mail address) from .claude/squad/notify.local.env (git-ignored).
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
EVENT="${1:-info}"; MESSAGE="${2:-}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# squad meta dir: .claude/squad when installed (primary), else .kiro/squad
SQ="$ROOT/.claude/squad"; [[ -d "$SQ" ]] || SQ="$ROOT/.kiro/squad"
CONFIG="$SQ/config.env"
SECRETS="$SQ/notify.local.env"
get() { [[ -f "$1" ]] && grep "^$2=" "$1" | head -1 | cut -d= -f2- || true; }

CHANNELS="$(get "$CONFIG" NOTIFY_CHANNELS)"; CHANNELS="${CHANNELS:-chat}"
PROJECT="$(basename "$ROOT")"
TEXT="[squad:$PROJECT] $EVENT — $MESSAGE"
json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
post_json() {  # url json
  command -v curl >/dev/null 2>&1 || { echo "notify: curl not found" >&2; return 1; }
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "$2" "$1" >/dev/null
}

rc=0
IFS=',' read -r -a list <<< "$CHANNELS"
for ch in "${list[@]}"; do
  ch="$(echo "$ch" | tr -d ' ')"
  case "$ch" in
    chat|push|"") ;;  # handled by the orchestrator in the Claude session
    desktop)
      if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$(json_escape "$MESSAGE")\" with title \"squad: $EVENT\"" || rc=1
      elif command -v notify-send >/dev/null 2>&1; then
        notify-send "squad: $EVENT" "$MESSAGE" || rc=1
      fi ;;
    slack)
      url="$(get "$SECRETS" SLACK_WEBHOOK_URL)"
      [[ -n "$url" ]] && { post_json "$url" "{\"text\":\"$(json_escape "$TEXT")\"}" || rc=1; } || echo "notify: SLACK_WEBHOOK_URL not set" >&2 ;;
    teams)
      url="$(get "$SECRETS" TEAMS_WEBHOOK_URL)"
      [[ -n "$url" ]] && { post_json "$url" "{\"text\":\"$(json_escape "$TEXT")\"}" || rc=1; } || echo "notify: TEAMS_WEBHOOK_URL not set" >&2 ;;
    webhook)
      url="$(get "$SECRETS" WEBHOOK_URL)"
      [[ -n "$url" ]] && { post_json "$url" "{\"project\":\"$(json_escape "$PROJECT")\",\"event\":\"$(json_escape "$EVENT")\",\"message\":\"$(json_escape "$MESSAGE")\"}" || rc=1; } || echo "notify: WEBHOOK_URL not set" >&2 ;;
    email)
      to="$(get "$SECRETS" EMAIL_TO)"
      if [[ -n "$to" ]] && command -v mail >/dev/null 2>&1; then printf '%s\n' "$TEXT" | mail -s "squad: $EVENT" "$to" || rc=1
      else echo "notify: EMAIL_TO not set or 'mail' not available" >&2; fi ;;
    *) echo "notify: unknown channel '$ch'" >&2 ;;
  esac
done
echo "$TEXT"
exit $rc
