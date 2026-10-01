#!/usr/bin/env bash
# .claude/hooks/squad-test-guard.sh — PreToolUse hook (Write|Edit|MultiEdit) declared by squad-backend,
# squad-frontend and squad-qa. Tests are never skipped, focused or silenced to get green: denies an edit that adds
# .skip / .only / xit / @pytest.mark.skip / @Disabled / t.Skip / #[ignore] / .todo to a test file.
# A test that is wrong is reported in the HANDOFF, not disabled. Needs jq; without jq it allows the edit.
set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')"
[[ -n "$FILE" ]] || exit 0
case "$FILE" in
  *test*|*spec*|*Test*|*Spec*|*/e2e/*) ;;
  *) exit 0 ;;
esac
PAT='\.(skip|only|todo)\(|(^|[^A-Za-z_])x(it|describe|test)\(|@pytest\.mark\.(skip|xfail)|pytest\.skip\(|@(Disabled|Ignore)([^A-Za-z]|$)|t\.Skip(Now|f)?\(|#\[ignore\]|\.skipIf\(|test\.fixme\('
count() { grep -cE "$PAT" 2>/dev/null || true; }
TOOL="$(printf '%s' "$INPUT" | jq -r '.tool_name')"
case "$TOOL" in
  Write) new="$(printf '%s' "$INPUT" | jq -r '.tool_input.content // ""' | count)"
         old="$( [[ -f "$FILE" ]] && count < "$FILE" || echo 0)" ;;
  Edit)  new="$(printf '%s' "$INPUT" | jq -r '.tool_input.new_string // ""' | count)"
         old="$(printf '%s' "$INPUT" | jq -r '.tool_input.old_string // ""' | count)" ;;
  MultiEdit)
         new="$(printf '%s' "$INPUT" | jq -r '[.tool_input.edits[]?.new_string] | join("\n")' | count)"
         old="$(printf '%s' "$INPUT" | jq -r '[.tool_input.edits[]?.old_string] | join("\n")' | count)" ;;
  *) exit 0 ;;
esac
if [[ "${new:-0}" -gt "${old:-0}" ]]; then
  jq -n --arg r "squad-test-guard: this edit skips, focuses or silences a test in $(basename "$FILE"). Fix the code or the test; if the test itself is wrong, leave it failing and report it in your HANDOFF (notes / contract_issue / spec)." \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
fi
exit 0
