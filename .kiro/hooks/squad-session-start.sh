#!/usr/bin/env bash
# .kiro/hooks/squad-session-start.sh — agentSpawn hook (Kiro) / SessionStart hook (Claude Code).
# On every new session, load the unfinished squad work (highest priority first, numbered) so you can pick one
# to continue. Also warns about the legacy document layout. Reads cwd from the hook event JSON on stdin;
# falls back to $CLAUDE_PROJECT_DIR then $PWD.
set -uo pipefail
ROOT=""
if command -v jq >/dev/null 2>&1 && [[ ! -t 0 ]]; then
  INPUT="$(cat 2>/dev/null || true)"
  ROOT="$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)"
fi
[[ -n "$ROOT" ]] && ROOT="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || echo "$ROOT")"
[[ -z "$ROOT" ]] && ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"

# Legacy layout gate first (blocks new features until migrated).
LEG=""; [[ -x "$ROOT/scripts/squad/layout.sh" ]] && LEG="$(cd "$ROOT" && scripts/squad/layout.sh legacy 2>/dev/null)" || true
[[ "$LEG" == LEGACY_LAYOUT* ]] && printf '%s\nNo new squad feature may start until scripts/squad/migrate-layout.sh has run.\n\n' "$LEG"

# The prioritised to-do list is produced by coord.sh (shared by both backends and by the /squad status/todo command).
COORD="$ROOT/scripts/squad/coord.sh"
if [[ -x "$COORD" ]]; then
  out="$(cd "$ROOT" && "$COORD" todos 2>/dev/null)" || out=""
  # Only print when there is unfinished work (coord prints a "No unfinished…" line we skip at session start).
  case "$out" in
    "Unfinished work"*) printf '%s\n' "$out" ;;
  esac
fi
exit 0
