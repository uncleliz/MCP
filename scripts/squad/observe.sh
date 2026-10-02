#!/usr/bin/env bash
# scripts/squad/observe.sh [feature-slug] — a read-only dashboard over the squad's own records.
# It turns what every feature already writes to state.json (stage, history timestamps, token totals, loop
# counters, gates, waiting) into a status table plus alerts, so you can see at a glance where work stands,
# where it is stuck, and where cost is running hot — without reading each file by hand.
#
#   observe.sh                 all features: one row each, then alerts
#   observe.sh <slug>          one feature: stage timeline, tokens, loops, alerts
#   observe.sh --alerts        only the alerts (good for a cron / notify hook)
#   observe.sh --json          machine-readable snapshot of all features
#
# Scope: this reads the squad's coordination records only (state.json). It is NOT runtime tracing of the
# agents themselves (no OpenTelemetry, no live token metering) — token figures are whatever each stage
# recorded in history. Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cfg() { for c in "$ROOT/.claude/squad/config.env" "$ROOT/.kiro/squad/config.env" "$ROOT/.opencode/squad/config.env"; do [[ -f "$c" ]] && { grep "^$1=" "$c" | head -1 | cut -d= -f2-; return; }; done; }

command -v jq >/dev/null 2>&1 || { echo "observe needs jq" >&2; exit 2; }

# Thresholds for alerts (config, with safe defaults).
STALL_MIN="${SQUAD_STALL_MIN:-$(cfg SQUAD_STALL_MIN)}"; STALL_MIN="${STALL_MIN:-120}"   # a stage unchanged this long = possible hang
TOKEN_WARN_PCT="${SQUAD_TOKEN_WARN_PCT:-$(cfg SQUAD_TOKEN_WARN_PCT)}"; TOKEN_WARN_PCT="${TOKEN_WARN_PCT:-80}"  # % of budget before warning
# Loop ceilings mirror the orchestrator's (squad skill §4).
declare -a LOOP_NAMES=(plan qa review spec env dod)
loop_ceiling() { case "$1" in plan) echo 2 ;; qa) echo 3 ;; review) echo 2 ;; spec) echo 2 ;; env) echo 2 ;; dod) echo 2 ;; *) echo 99 ;; esac; }

epoch_now() { date -u +%s; }
# Portable ISO-8601 → epoch (GNU date and BSD/macOS date).
iso_epoch() {
  local t="$1"; [[ -z "$t" || "$t" == null ]] && { echo 0; return; }
  date -u -d "$t" +%s 2>/dev/null && return 0
  date -u -j -f '%Y-%m-%dT%H:%M:%S%z' "${t/Z/+0000}" +%s 2>/dev/null && return 0
  date -u -j -f '%Y-%m-%dT%H:%M:%S' "${t%%[+Z]*}" +%s 2>/dev/null && return 0
  echo 0
}

state_files() {
  for f in "$ROOT"/docs/squad/features/*/state.json \
           "$ROOT"/.claude/worktrees/squad-*/docs/squad/features/*/state.json \
           "$ROOT"/.kiro/worktrees/squad-*/docs/squad/features/*/state.json \
           "$ROOT"/.opencode/worktrees/squad-*/docs/squad/features/*/state.json; do
    [[ -f "$f" ]] && echo "$f"
  done
}

# Compute per-feature facts into TSV: feat stage tier tokens budget_m last_at loops_json gates_json waiting paused escalation file
# Empty text fields are emitted as "-" so adjacent tabs are not collapsed by read's IFS-whitespace handling.
facts() {
  local f r
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    r="$(jq -r '[
        (.feature // "?"), (.stage // "?"), (.tier // "?"),
        (.tokens.total // 0), (.tokens.budget_m // 0),
        ((.history // []) | (last.at) // "-"),
        (.loops // {} | tojson), (.gates // {} | tojson),
        ((.waiting // "") | if . == "" then "-" else . end),
        ((.paused // "") | if . == "" then "-" else . end),
        ((.escalation // "") | if . == "" then "-" else . end)
      ] | @tsv' "$f" 2>/dev/null)" || continue
    printf '%s\t%s\n' "$r" "$f"
  done < <(state_files)
}
# Turn the "-" placeholder back into an empty string.
unph() { [[ "$1" == "-" ]] && echo "" || echo "$1"; }

# Build alerts for one feature's facts; echo one line per alert (SEVERITY<TAB>feature<TAB>message).
alerts_for() {
  local feat="$1" stage="$2" tokens="$3" budget_m="$4" last_at="$5" loops_json="$6" waiting="$7" paused="$8" esc="$9"
  # 1) Stuck loop: any counter at/above its ceiling.
  local n ceil val
  for n in "${LOOP_NAMES[@]}"; do
    val="$(printf '%s' "$loops_json" | jq -r --arg k "$n" '.[$k] // 0' 2>/dev/null)"; val="${val:-0}"
    ceil="$(loop_ceiling "$n")"
    if [[ "$val" =~ ^[0-9]+$ && "$val" -ge "$ceil" ]]; then
      printf 'WARN\t%s\tstuck: loop "%s" at %s (ceiling %s) — likely looping on %s\n' "$feat" "$n" "$val" "$ceil" "$stage"
    fi
  done
  # 2) Cost hot: tokens over TOKEN_WARN_PCT% of the budget (only when a budget is set).
  if [[ "$budget_m" =~ ^[0-9]+$ && "$budget_m" -gt 0 && "$tokens" =~ ^[0-9]+$ ]]; then
    local pct=$(( tokens * 100 / (budget_m * 1000000) ))
    if [[ "$pct" -ge 100 ]]; then printf 'ALERT\t%s\tcost: %s tokens over budget (%sM) — %s%%\n' "$feat" "$tokens" "$budget_m" "$pct"
    elif [[ "$pct" -ge "$TOKEN_WARN_PCT" ]]; then printf 'WARN\t%s\tcost: %s tokens = %s%% of the %sM budget\n' "$feat" "$tokens" "$pct" "$budget_m"; fi
  fi
  # 3) Possible hang: last activity older than STALL_MIN, and not parked (not paused, not a CEO gate, not waiting).
  if [[ -z "$paused" && -z "$waiting" && "$stage" != ceo-plan && "$stage" != ceo-golive && "$stage" != "done" && "$stage" != closed ]]; then
    local e_last age_min
    e_last="$(iso_epoch "$last_at")"
    if [[ "$e_last" -gt 0 ]]; then
      age_min=$(( ($(epoch_now) - e_last) / 60 ))
      [[ "$age_min" -ge "$STALL_MIN" ]] && printf 'WARN\t%s\tno activity for %s min at stage %s (threshold %s) — possible hang\n' "$feat" "$age_min" "$stage" "$STALL_MIN"
    fi
  fi
  # 4) Escalation pending (needs the CEO).
  [[ -n "$esc" && "$esc" != none ]] && printf 'ALERT\t%s\tescalation pending — needs the CEO\n' "$feat"
}

MODE="${1:-all}"

if [[ "$MODE" == "--json" ]]; then
  facts | while IFS=$'\t' read -r feat stage tier tokens budget_m last_at loops gates waiting paused esc f; do
    last_at="$(unph "$last_at")"; waiting="$(unph "$waiting")"; paused="$(unph "$paused")"; esc="$(unph "$esc")"
    jq -nc --arg feat "$feat" --arg stage "$stage" --arg tier "$tier" --argjson tokens "${tokens:-0}" \
      --argjson budget_m "${budget_m:-0}" --arg last_at "$last_at" --argjson loops "${loops:-{}}" \
      --argjson gates "${gates:-{}}" --arg waiting "$waiting" --arg paused "$paused" --arg esc "$esc" \
      '{feature:$feat,stage:$stage,tier:$tier,tokens:$tokens,budget_m:$budget_m,last_at:$last_at,loops:$loops,gates:$gates,waiting:$waiting,paused:$paused,escalation:$esc}'
  done
  exit 0
fi

print_alerts() {
  local any=0 out
  out="$(facts | while IFS=$'\t' read -r feat stage tier tokens budget_m last_at loops gates waiting paused esc f; do
    last_at="$(unph "$last_at")"; waiting="$(unph "$waiting")"; paused="$(unph "$paused")"; esc="$(unph "$esc")"
    alerts_for "$feat" "$stage" "$tokens" "$budget_m" "$last_at" "$loops" "$waiting" "$paused" "$esc"
  done)"
  if [[ -n "$out" ]]; then
    echo "Alerts:"
    printf '%s\n' "$out" | sort | while IFS=$'\t' read -r sev feat msg; do
      printf '  [%s] %-24s %s\n' "$sev" "$feat" "$msg"
    done
    any=1
  fi
  [[ $any -eq 0 ]] && echo "Alerts: none"
}

if [[ "$MODE" == "--alerts" ]]; then print_alerts; exit 0; fi

if [[ "$MODE" != "all" && "$MODE" != --* ]]; then
  # Single feature detail.
  slug="$MODE"; found=""
  while IFS= read -r f; do [[ "$(jq -r '.feature // ""' "$f" 2>/dev/null)" == "$slug" ]] && { found="$f"; break; }; done < <(state_files)
  [[ -n "$found" ]] || { echo "no feature '$slug'" >&2; exit 1; }
  jq -r '
    "Feature: \(.feature)   stage=\(.stage)   tier=\(.tier)",
    "Tokens:  \(.tokens.total // 0)\(if (.tokens.budget_m//0) > 0 then " / \(.tokens.budget_m)M budget" else " (no budget)" end)",
    "Gates:   plan=\(.gates.ceo_plan // "?")  golive=\(.gates.ceo_golive // "?")",
    (if (.waiting // "") != "" then "Waiting: \(.waiting)" else empty end),
    (if (.paused // "") != "" then "Paused:  \(.paused)" else empty end),
    "",
    "Timeline (recent):",
    ( .history // [] | (if length > 12 then .[-12:] else . end)[]
      | "  \(.at // "?")  \(.stage // "?")  \(.result // "?")\(if (.tokens // null) != null then "  \(.tokens)k" else "" end)\(if (.note // "") != "" then "  — \(.note)" else "" end)" )
  ' "$found"
  echo
  r="$(jq -r '[(.feature//"?"),(.stage//"?"),(.tier//"?"),(.tokens.total//0),(.tokens.budget_m//0),((.history//[])|(last.at)//"-"),(.loops//{}|tojson),((.waiting//"")|if .=="" then "-" else . end),((.paused//"")|if .=="" then "-" else . end),((.escalation//"")|if .=="" then "-" else . end)] | @tsv' "$found")"
  IFS=$'\t' read -r feat stage tier tokens budget_m last_at loops waiting paused esc <<< "$r"
  last_at="$(unph "$last_at")"; waiting="$(unph "$waiting")"; paused="$(unph "$paused")"; esc="$(unph "$esc")"
  a="$(alerts_for "$feat" "$stage" "$tokens" "$budget_m" "$last_at" "$loops" "$waiting" "$paused" "$esc")"
  if [[ -n "$a" ]]; then echo "Alerts:"; printf '%s\n' "$a" | sort | while IFS=$'\t' read -r sev _ff msg; do printf '  [%s] %s\n' "$sev" "$msg"; done; else echo "Alerts: none"; fi
  exit 0
fi

# Default: table of all features + alerts.
rows="$(facts)"
if [[ -z "$rows" ]]; then echo "No squad features yet."; exit 0; fi
printf '%-26s %-16s %-9s %-14s %-10s\n' "FEATURE" "STAGE" "TIER" "TOKENS" "GATES"
printf '%-26s %-16s %-9s %-14s %-10s\n' "-------" "-----" "----" "------" "-----"
printf '%s\n' "$rows" | while IFS=$'\t' read -r feat stage tier tokens budget_m last_at loops gates waiting paused esc f; do
  case "$stage" in done|closed) continue ;; esac
  tok="$tokens"; [[ "${budget_m:-0}" =~ ^[0-9]+$ && "${budget_m}" -gt 0 ]] && tok="$tokens/${budget_m}M"
  g="$(printf '%s' "$gates" | jq -r '"\(.ceo_plan // "?")/\(.ceo_golive // "?")"' 2>/dev/null)"
  printf '%-26s %-16s %-9s %-14s %-10s\n' "$feat" "$stage" "$tier" "$tok" "$g"
done
echo
print_alerts
