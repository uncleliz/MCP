#!/usr/bin/env bash
# scripts/squad/coord.sh — coordination between squad features that run at the same time (one per worktree).
# State lives in the git common dir, so every worktree of the repository sees the same locks and counters.
#   coord.sh lock <name> <slug> [--wait SECONDS]   take a lock (idempotent for the same slug); exit 75 if held by another
#   coord.sh unlock <name> <slug> [--force]        release it (only the holder; --force is for the CEO in a terminal)
#   coord.sh holder <name>                         print the holding slug (empty if free)
#   coord.sh locks                                 list held locks with age
#   coord.sh release-all <slug>                    release every lock held by a feature (closed / done)
#   coord.sh todos                                 list unfinished features, highest priority first, numbered, with resume commands
#   coord.sh next-adr                              allocate the next ADR number (4 digits), unique across worktrees
#   coord.sh next-lesson                           allocate the next lesson id (L-nnn), unique across worktrees
# Lock names: env-uat, env-pre, env-prod (a shared environment), main (merging / committing on the main branch).
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
if common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  STORE="$common/squad"; MAIN="${common%/.git}"
elif common="$(git rev-parse --git-common-dir 2>/dev/null)"; then
  common="$(cd "$common" && pwd)"; STORE="$common/squad"; MAIN="${common%/.git}"
else
  MAIN="$(cd "$(dirname "$0")/../.." && pwd)"; STORE="$MAIN/.claude/squad/coord"; [[ -d "$MAIN/.claude/squad" ]] || STORE="$MAIN/.kiro/squad/coord"
fi
mkdir -p "$STORE/locks"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
epoch() { date -u +%s; }
valid_name() { [[ "$1" =~ ^(env-uat|env-pre|env-prod|main)$ ]] || { echo "unknown lock '$1' (env-uat, env-pre, env-prod, main)" >&2; exit 64; }; }
valid_slug() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid slug '$1'" >&2; exit 64; }; }
holder() { cut -d'|' -f1 "$STORE/locks/$1.lock/owner" 2>/dev/null || true; }
mutex() {  # mutex <name> <command…> — short critical section (mkdir is atomic on every OS)
  local m="$STORE/$1.mutex" i=0; shift
  until mkdir "$m" 2>/dev/null; do i=$((i + 1)); [[ $i -gt 100 ]] && { echo "timeout waiting for $m (remove it if stale)" >&2; exit 75; }; sleep 0.1; done
  "$@"; local rc=$?; rmdir "$m"; return $rc
}

cmd="${1:-}"; shift || true
case "$cmd" in
  lock)
    name="${1:-}"; slug="${2:-}"; valid_name "$name"; valid_slug "$slug"; wait=0
    [[ "${3:-}" == --wait ]] && wait="${4:-0}"
    deadline=$(( $(epoch) + wait ))
    while :; do
      if mkdir "$STORE/locks/$name.lock" 2>/dev/null; then
        printf '%s|%s|%s|%s\n' "$slug" "$(now)" "$(epoch)" "$(hostname 2>/dev/null || echo host)" > "$STORE/locks/$name.lock/owner"
        echo "locked $name for $slug"; exit 0
      fi
      h="$(holder "$name")"
      [[ "$h" == "$slug" ]] && { echo "$name already held by $slug"; exit 0; }
      if [[ $(epoch) -ge $deadline ]]; then
        since="$(cut -d'|' -f2 "$STORE/locks/$name.lock/owner" 2>/dev/null)"
        echo "BUSY: $name is held by '${h:-?}' since ${since:-?} — wait, or work on another stage" >&2; exit 75
      fi
      sleep 5
    done ;;
  unlock)
    name="${1:-}"; slug="${2:-}"; valid_name "$name"
    [[ -d "$STORE/locks/$name.lock" ]] || { echo "$name is free"; exit 0; }
    h="$(holder "$name")"
    if [[ "$h" != "$slug" && "${3:-}" != --force ]]; then echo "REFUSED: $name is held by '$h', not '$slug'" >&2; exit 65; fi
    rm -rf "${STORE:?}/locks/$name.lock"; echo "unlocked $name" ;;
  holder) valid_name "${1:-}"; holder "$1" ;;
  locks)
    found=0
    for d in "$STORE"/locks/*.lock; do
      [[ -d "$d" ]] || continue; found=1
      IFS='|' read -r s t e h < "$d/owner"
      age=$(( ($(epoch) - ${e:-$(epoch)}) / 60 ))
      printf '%-9s %-24s since %s (%s min)%s\n' "$(basename "$d" .lock)" "$s" "$t" "$age" "$([[ $age -gt 1440 ]] && echo '  ← older than 24 h, check it')"
    done
    [[ $found -eq 1 ]] || echo "no locks held" ;;
  release-all)
    slug="${1:-}"; valid_slug "$slug"
    for d in "$STORE"/locks/*.lock; do
      [[ -d "$d" ]] || continue
      [[ "$(cut -d'|' -f1 "$d/owner")" == "$slug" ]] && { rm -rf "${d:?}"; echo "unlocked $(basename "$d" .lock)"; }
    done; exit 0 ;;
  next-adr)
    _adr() {
      local max=0 n f
      n="$(cat "$STORE/adr.counter" 2>/dev/null || echo 0)"; max=$((10#${n:-0}))
      for f in "$MAIN"/docs/adr/[0-9]*.md "$MAIN"/.claude/worktrees/*/docs/adr/[0-9]*.md "$MAIN"/.kiro/worktrees/*/docs/adr/[0-9]*.md; do
        [[ -f "$f" ]] || continue; n="$(basename "$f" | grep -oE '^[0-9]+')"; [[ $((10#$n)) -gt $max ]] && max=$((10#$n))
      done
      printf '%d\n' $((max + 1)) > "$STORE/adr.counter"; printf '%04d\n' $((max + 1))
    }
    mutex adr _adr ;;
  next-lesson)
    _les() {
      local max=0 n f
      n="$(cat "$STORE/lesson.counter" 2>/dev/null || echo 0)"; max=$((10#${n:-0}))
      for f in "$MAIN"/docs/squad/knowledge/lessons.md "$MAIN"/.claude/worktrees/*/docs/squad/knowledge/lessons.md "$MAIN"/.kiro/worktrees/*/docs/squad/knowledge/lessons.md; do
        [[ -f "$f" ]] || continue
        n="$(grep -oE '^## L-[0-9]+' "$f" | grep -oE '[0-9]+' | sort -n | tail -1)"; [[ -n "$n" && $((10#$n)) -gt $max ]] && max=$((10#$n))
      done
      printf '%d\n' $((max + 1)) > "$STORE/lesson.counter"; printf 'L-%03d\n' $((max + 1))
    }
    mutex lesson _les ;;
  todos)
    # Load every unfinished feature, sorted by priority, numbered, each with its resume command.
    # Priority (highest first): 1 in-progress but stalled (squad can continue now), 2 waiting on a CEO gate,
    # 3 escalation pending, 4 open S1/S2 defect, 5 waiting on an env lock. done/closed are omitted.
    command -v jq >/dev/null 2>&1 || { echo "todos needs jq"; exit 0; }
    ES="$MAIN/scripts/squad/errors.sh"
    rows=""; seen=""
    for f in "$MAIN"/docs/squad/features/*/state.json \
             "$MAIN"/.claude/worktrees/squad-*/docs/squad/features/*/state.json \
             "$MAIN"/.kiro/worktrees/squad-*/docs/squad/features/*/state.json; do
      [[ -f "$f" ]] || continue
      r="$(jq -r '[.feature, .stage, (.tier//"?"), (.gates.ceo_plan//"?"), (.gates.ceo_golive//"?"), (.waiting//"-"), (.escalation//"-"), (.paused//"-")] | @tsv' "$f" 2>/dev/null)" || continue
      IFS=$'\t' read -r feat stage tier g1 g2 waiting esc paused <<< "$r"
      # "-" is the empty-field placeholder (keeps tab columns aligned; bash collapses adjacent empty tabs)
      [[ "$waiting" == "-" ]] && waiting=""; [[ "$esc" == "-" ]] && esc=""; [[ "$paused" == "-" ]] && paused=""
      feat="${feat:-$(basename "$(dirname "$f")")}"
      case " $seen " in *" $feat "*) continue ;; esac; seen="$seen $feat"
      case "$stage" in done|closed) continue ;; esac
      where="main checkout"; case "$f" in *"/worktrees/"*) where="worktree" ;; esac
      # open S1/S2 for this feature?
      sev=""
      if [[ -x "$ES" ]]; then sev="$(cd "$MAIN" && "$ES" list --open --severity S1,S2 --feature "$feat" 2>/dev/null | tail -n +2)"; fi
      # classify
      prio=1; label="in progress — ready to continue"
      if [[ -n "$esc" && "$esc" != none ]]; then prio=3; label="CTO escalation — needs your answer"
      elif [[ "$stage" == *ceo-plan* || "$g1" == pending ]] && [[ "$stage" != backend && "$stage" != frontend ]]; then
        if [[ "$stage" == ceo-plan || "$stage" == cto-plan-review ]]; then prio=2; label="waiting for your Gate 1 (plan) approval"; fi
      fi
      [[ "$stage" == ceo-golive || "$stage" == cab-pack || "$g2" == ready ]] && { prio=2; label="waiting for your Gate 2 (go-live) approval"; }
      [[ "$stage" == ceo-plan ]] && { prio=2; label="waiting for your Gate 1 (plan) approval"; }
      [[ "$stage" == ceo-golive ]] && { prio=2; label="waiting for your Gate 2 (go-live) approval"; }
      if [[ -n "$sev" ]]; then [[ $prio -gt 4 || $prio -eq 1 ]] && { prio=4; label="open S1/S2 defect — needs attention"; }; fi
      [[ -n "$waiting" ]] && { prio=5; label="waiting on a lock: $waiting"; }
      # Paused by the CEO (preempted by other work) — keep it in the top group so it is easy to resume.
      [[ -n "$paused" ]] && { prio=1; label="paused — $paused"; }
      rows="$rows$prio	$feat	$stage	$tier	$where	$label"$'\n'
    done
    if [[ -z "$rows" ]]; then echo "No unfinished squad features. Start one with: @squad <goal>  (Kiro)  or  /squad <goal>  (Claude Code)"; exit 0; fi
    echo "Unfinished work, highest priority first — pick one to continue:"
    i=0
    printf '%s' "$rows" | sort -t$'\t' -k1,1n -k2,2 | while IFS=$'\t' read -r _prio feat stage tier where label; do
      [[ -z "$feat" ]] && continue
      i=$((i+1))
      printf '  [%d] %-28s %s\n      stage=%s tier=%s (%s) → continue %s\n' "$i" "$feat" "$label" "$stage" "$tier" "$where" "$feat"
    done
    echo "Type:  continue <feature>   (or the number shown)."
    exit 0 ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64 ;;
esac
