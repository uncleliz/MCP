#!/usr/bin/env bash
# .kiro/hooks/squad-artifact-lint.sh — postToolUse hook for file writes (Kiro) / PostToolUse Write|Edit|MultiEdit (Claude Code).
# Right after a squad document is written, runs scripts/squad/check.sh on it (target from scripts/squad/layout.sh)
# and feeds FAIL/WARN lines back to
# the agent, so gaps are fixed while the file is open instead of at the end. Never blocks. Needs jq.
set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"
case "$FILE" in */docs/squad/*|*/.kiro/rules/squad-learned-*|*/.claude/rules/squad-learned-*) ;; *) exit 0 ;; esac
# The file's own checkout decides the root: a file inside a feature worktree belongs to that worktree, even when the
# session runs in the main checkout (and the other way round), as long as both are worktrees of the same repository.
gcd() { local c; c="$(git -C "$1" rev-parse --git-common-dir 2>/dev/null)" || return 0; (cd "$1" && cd "$c" && pwd -P); }
file_root() {
  local d; d="$(dirname "$1")"; while [[ ! -d "$d" && "$d" != / && "$d" != . ]]; do d="$(dirname "$d")"; done
  git -C "$d" rev-parse --show-toplevel 2>/dev/null || true
}
CWD="$(printf '%s' "$INPUT" | jq -r '.cwd // empty')"; ROOT="$(git -C "${CWD:-$(dirname "$FILE")}" rev-parse --show-toplevel 2>/dev/null || echo "${CWD:-.}")"
FROOT="$(file_root "$FILE")"
if [[ -n "$FROOT" && "$FROOT" != "$ROOT" && "$(gcd "$FROOT")" == "$(gcd "$ROOT")" ]]; then ROOT="$FROOT"; fi
S="$ROOT/scripts/squad"
[[ -x "$S/check.sh" && -x "$S/layout.sh" ]] || exit 0
C="$(SQUAD_ROOT="$ROOT" "$S/layout.sh" classify "$FILE")"
TARGET="$(printf '%s' "$C" | cut -f4)"; SLUG="$(printf '%s' "$C" | cut -f5)"; EXTRA="$(printf '%s' "$C" | cut -f6)"
BASE="$(basename "$FILE")"
case "$TARGET" in ""|-|UNKNOWN*) exit 0 ;; esac
case "$TARGET" in
  lessons|distill-log) out="$("$S/check.sh" "$TARGET" "$ROOT/docs/squad/knowledge" 2>&1)"; args="$TARGET" ;;
  handbook) out="$("$S/check.sh" handbook "$ROOT/docs/squad/knowledge" "$EXTRA" 2>&1)"; args="handbook $EXTRA" ;;
  learned) out="$("$S/check.sh" learned "$ROOT" "$EXTRA" 2>&1)"; args="learned $EXTRA" ;;
  *) # shellcheck disable=SC2086  # EXTRA is empty or one word (env)
     out="$("$S/check.sh" "$TARGET" "$ROOT/docs/squad/features/$SLUG" $EXTRA 2>&1)"; args="$TARGET $EXTRA" ;;
esac
issues="$(printf '%s\n' "$out" | grep -E '^(FAIL|WARN):')"
if [[ "$BASE" == api-contract.yaml ]]; then   # a real OpenAPI linter when the project has one
  if command -v redocly >/dev/null 2>&1; then lint="$(redocly lint "$FILE" 2>&1)" || issues="$issues"$'\n'"FAIL: redocly lint: $(printf '%s' "$lint" | grep -iE 'error' | head -5)"
  elif command -v spectral >/dev/null 2>&1; then lint="$(spectral lint "$FILE" 2>&1)" || issues="$issues"$'\n'"FAIL: spectral lint: $(printf '%s' "$lint" | grep -iE 'error' | head -5)"
  fi
  issues="$(printf '%s' "$issues" | sed '/^$/d')"
fi
[[ -z "$issues" ]] && exit 0
jq -n --arg c "scripts/squad/check.sh $args on $BASE (Definition of Done — fix before handing off; a missing section may simply not be written yet):"$'\n'"$issues" \
  '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$c}}'
exit 0
