#!/usr/bin/env bash
# .kiro/hooks/squad-bash-guard.sh — preToolUse hook for Bash/execute_bash (Kiro) / PreToolUse (Claude Code).
#   * everyone: no force push, no --no-verify.
#   * squad-* subagents: no git history changes (commit/merge/push/rebase/reset/tag/branch/worktree —
#     the Delivery Manager does git), no production deploy except squad-release, and no shell writes to
#     state.json / plan-approval.md / cab-approval.md / the append-only ledgers.
# Needs jq; without jq it allows everything (and --doctor warns).
#
# Platform note. The role name is the first argument ($1), set in each squad-* agent's hook command
# (Kiro passes no agent_type/agent_id). When $1 is a squad-* role, this runs for a subagent; the Delivery
# Manager registers the hook with no arg, so the subagent-only rules below are skipped for it. On Claude
# Code the arg is absent and the role/subagent signal comes from .agent_type / .agent_id over stdin.
set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
ROLE_ARG="${1:-}"
INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"
AGENT="$ROLE_ARG"; [[ -z "$AGENT" ]] && AGENT="$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')"; AGENT="${AGENT##*:}"
AGENT_ID="$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')"
# Kiro gives no agent_id; a squad-* role arg is the subagent signal there.
[[ -z "$AGENT_ID" && "$AGENT" == squad-* ]] && AGENT_ID="$AGENT"
[[ -z "$CMD" ]] && exit 0
deny() {
  jq -n --arg r "squad-bash-guard: $1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}
has() { printf '%s' "$CMD" | grep -Eq -- "$1"; }

has '(^|[;&|[:space:]])git([[:space:]]+-[Cc][[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]].*)?[[:space:]](--force(-with-lease)?|-f)([[:space:]=]|$)' \
  && deny "force push is not allowed; history on shared branches is never rewritten. Run it yourself in a terminal if you really mean it."
has '(^|[;&|[:space:]])git[[:space:]].*(commit|push|merge)([[:space:]].*)?[[:space:]]--no-verify' \
  && deny "--no-verify skips the project's checks; fix what the hook reports instead."

[[ -z "$AGENT_ID" ]] && exit 0   # the rest applies to subagents only

# Shared environments: a deploy needs the env lock held by this feature (scripts/squad/coord.sh), so two features
# running side by side never overwrite each other's UAT/PRE/PROD deployment. Rollback is never blocked.
if ENVN="$(printf '%s' "$CMD" | grep -oE 'deploy\.sh[[:space:]]+(uat|pre|prod)' | awk '{print $2}' | head -1)" && [[ -n "$ENVN" ]]; then
  CWD="$(printf '%s' "$INPUT" | jq -r '.cwd // empty')"; CWD="${CWD:-$PWD}"
  SLUG="$(printf '%s' "$CWD" | sed -n 's#.*/\.claude/worktrees/squad-\([a-z0-9-]*\).*#\1#p')"
  if [[ -z "$SLUG" ]]; then   # main checkout: the single feature in progress there
    for st in "$CWD"/docs/squad/features/*/state.json; do
      [[ -f "$st" ]] || continue
      grep -qE '"stage" *: *"(done|closed)"' "$st" || SLUG="${SLUG:+many}${SLUG:-$(basename "$(dirname "$st")")}"
    done
  fi
  COORD="$CWD/scripts/squad/coord.sh"
  if [[ -x "$COORD" && -n "$SLUG" && "$SLUG" != many* ]]; then
    HOLDER="$(cd "$CWD" && "$COORD" holder "env-$ENVN" 2>/dev/null)"
    [[ "$HOLDER" == "$SLUG" ]] || deny "deploy to $ENVN needs the env-$ENVN lock held by '$SLUG' (now: '${HOLDER:-free}'). The Delivery Manager takes it with scripts/squad/coord.sh lock env-$ENVN $SLUG before dispatching the deploy."
  fi
fi

PROTECTED='(state\.json|plan-approval\.md|cab-approval\.md|decisions\.md|errors\.md|lessons\.md|distill-log\.md|docs/squad/README\.md)'
has "(>>?|tee([[:space:]]+-a)?)[[:space:]]*[^[:space:];|&]*$PROTECTED" \
  && deny "shell writes to squad state, approvals or ledgers are not allowed from a subagent."
has "(sed[[:space:]]+-i|perl[[:space:]]+-pi|truncate|rm|mv|cp)[^;|&]*$PROTECTED" \
  && deny "shell writes to squad state, approvals or ledgers are not allowed from a subagent."

[[ "$AGENT" == squad-* ]] || exit 0
has '(^|[;&|[:space:]])git([[:space:]]+-[Cc][[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+(commit|merge|push|pull|rebase|reset|revert|cherry-pick|tag|branch[[:space:]]+-[dDmM]|checkout|switch|stash|worktree|am)([[:space:]]|$)' \
  && deny "$AGENT may not change git history or branches; the Delivery Manager commits. Read-only git (status, diff, log, show) is fine."
if [[ "$AGENT" != "squad-release" ]] && has 'deploy\.sh[[:space:]]+prod'; then
  deny "only squad-release deploys production, and only in mode deploy-prod after Gate 2."
fi
exit 0
