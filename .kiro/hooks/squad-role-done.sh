#!/usr/bin/env bash
# .kiro/hooks/squad-role-done.sh — stop hook declared in each squad-* agent (Kiro) / Stop hook (Claude Code).
# A role may finish only when its work meets the Definition of Done:
#   1. the final message ends with a HANDOFF block containing the role's required fields;
#   2. status: blocked names what blocks it; status: done passes scripts/squad/check.sh for the role's artifact,
#      and engineers report failed=0 and coverage ≥ 80 %.
# Otherwise the stop is blocked with the exact failures (at most twice per agent run, then it lets go and the
# Delivery Manager's own check catches it). Needs jq; without jq it allows the stop.
#
# Platform note. The role name is the first argument ($1), set in each agent's hook command
# (Kiro passes no agent_type/agent_id over stdin). On Claude Code the arg is absent and the role
# comes from .agent_type. The assistant text is .assistant_response on Kiro, .last_assistant_message on Claude.
set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
ROLE_ARG="${1:-}"
INPUT="$(cat)"
j() { printf '%s' "$INPUT" | jq -r "$1 // empty" 2>/dev/null; }
ROLE="$ROLE_ARG"; [[ -z "$ROLE" ]] && ROLE="$(j .agent_type)"; ROLE="${ROLE##*:}"
[[ "$ROLE" == squad-* ]] || exit 0
MSG="$(j .assistant_response)"; [[ -z "$MSG" ]] && MSG="$(j .last_assistant_message)"
CWD="$(j .cwd)"; [[ -z "$CWD" ]] && CWD="$PWD"
ROOT="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "$CWD")"
AID="$(j .agent_id)"; COUNTER="${TMPDIR:-/tmp}/squad-stop-${AID:-$ROLE}"
n="$(cat "$COUNTER" 2>/dev/null || echo 0)"
[[ "$n" -ge 2 ]] && { rm -f "$COUNTER"; exit 0; }

problems=""
add() { problems="${problems}- $1"$'\n'; }

# --- HANDOFF block: from the last line that is exactly "HANDOFF" to the end (fences dropped)
HO="$(printf '%s\n' "$MSG" | awk '/^[[:space:]]*HANDOFF[[:space:]]*$/ {buf = ""; on = 1; next} on {buf = buf $0 "\n"} END {printf "%s", buf}' | grep -v '^[[:space:]]*```')"
field() { printf '%s\n' "$HO" | sed -n "s/^[[:space:]]*$1:[[:space:]]*//p" | head -1 | sed 's/[[:space:]]*#.*$//'; }
if [[ -z "$HO" ]]; then
  add "End your final message with a HANDOFF block (squad-protocol §7 plus the fields in your role file)."
else
  case "$ROLE" in
    squad-po)         req="feature mode status artifacts" ;;
    squad-researcher) req="feature mode status artifacts sources" ;;
    squad-sa)         req="feature mode status artifacts uncovered_fr" ;;
    squad-ba)         req="feature status artifacts counts" ;;
    squad-lead)       req="feature status artifacts uncovered_ac schedule_delta_pct" ;;
    squad-qa)         req="feature mode env status verdict failures_by_class" ;;
    squad-backend|squad-frontend) req="feature status tasks_done tests contract_issue" ;;
    squad-reviewer)   req="feature status verdict counts" ;;
    squad-release)    req="feature mode status env result" ;;
    squad-cto)        req="feature mode status decision decision_id" ;;
    *)                req="status" ;;
  esac
  if [[ "$ROLE" == squad-cto ]] && printf '%s\n' "$HO" | grep -qE '^[[:space:]]*mode:[[:space:]]*distill'; then req="mode status decision distill_id"; fi
  for k in $req; do printf '%s\n' "$HO" | grep -qE "^[[:space:]]*$k:" || add "HANDOFF is missing '$k:'"; done
fi

STATUS="$(field status)"
if [[ -n "$HO" && ! "$STATUS" =~ ^(done|blocked)$ ]]; then add "HANDOFF status must be 'done' or 'blocked' (got '$STATUS')"; fi

if [[ "$STATUS" == blocked ]]; then
  printf '%s\n' "$HO" | awk '/^[[:space:]]*blocking:/ {on = 1; sub(/^[[:space:]]*blocking:[[:space:]]*/, ""); if (length) n++; next} on && /^[[:space:]]*-[[:space:]]*[^[:space:]]/ {n++; next} on && /^[[:space:]]*[a-z_]+:/ {on = 0} END {exit !(n > 0)}' \
    || add "status: blocked needs 'blocking:' with what is missing and who owns it"
fi

if [[ "$STATUS" == "done" && -n "$HO" ]]; then
  FEAT="$(field feature)"; DIR="$ROOT/docs/squad/features/$FEAT"
  if [[ -z "$FEAT" || ! -d "$DIR" ]]; then
    DIR="$(ls -td "$ROOT"/docs/squad/features/*/ 2>/dev/null | head -1)"; DIR="${DIR%/}"
  fi
  CHECK="$ROOT/scripts/squad/check.sh"
  MODE="$(field mode)"; ENVV="$(field env)"
  targets=""
  case "$ROLE" in
    squad-po)         case "$MODE" in brief) targets="brief" ;; *) targets="prd" ;; esac ;;
    squad-researcher) targets="research" ;;
    squad-sa)         case "$MODE" in options) targets="options" ;; *) targets="architecture contract" ;; esac
                      u="$(field uncovered_fr | tr -d '[] ')"; [[ -z "$u" ]] || add "status done with uncovered_fr: $u" ;;
    squad-ba)         targets="requirements" ;;
    squad-lead)       targets="plan"; u="$(field uncovered_ac | tr -d '[] ')"; [[ -z "$u" ]] || add "status done with uncovered_ac: $u" ;;
    squad-qa)         case "$MODE" in plan) targets="testplan" ;; *) targets="report:$ENVV" ;; esac ;;
    squad-reviewer)   targets="review" ;;
    squad-release)    case "$MODE" in cab-pack) targets="cabpack" ;; deploy-uat) targets="releaselog:uat" ;; deploy-pre) targets="releaselog:pre" ;; deploy-prod|watch) targets="releaselog:prod" ;; esac ;;
    squad-cto)        case "$MODE" in retro) targets="retro knowledge:lessons" ;; distill) targets="knowledge:lessons knowledge:distill-log knowledge:handbooks knowledge:learned" ;; *) targets="decisions" ;; esac ;;
    squad-backend|squad-frontend)
      t="$(field tests)"
      [[ "$t" =~ failed=0([^0-9]|$) ]] || add "status done requires 'tests: … failed=0' (got '$t')"
      cov="$(printf '%s' "$t" | sed -n 's/.*coverage=\([0-9][0-9]*\).*/\1/p')"
      [[ -n "$cov" && "$cov" -ge 80 ]] || add "status done requires coverage ≥ 80 on changed code in 'tests: … coverage=<pct>' (got '${cov:-none}')" ;;
  esac
  if [[ -n "$targets" && -x "$CHECK" ]] && { [[ -n "$DIR" ]] || [[ "$MODE" == distill ]]; }; then
    KD="$ROOT/docs/squad/knowledge"
    for tg in $targets; do
      case "$tg" in
        knowledge:lessons|knowledge:distill-log)
          out="$("$CHECK" "${tg#knowledge:}" "$KD" 2>&1)" || add "scripts/squad/check.sh ${tg#knowledge:} failed:"$'\n'"$(printf '%s\n' "$out" | grep -E '^FAIL:' | sed 's/^/    /')"
          continue ;;
        knowledge:handbooks)
          for h in "$KD"/roles/*.md; do [[ -f "$h" ]] || continue
            out="$("$CHECK" handbook "$KD" "$(basename "$h" .md)" 2>&1)" || add "check.sh handbook $(basename "$h" .md) failed:"$'\n'"$(printf '%s\n' "$out" | grep -E '^FAIL:' | sed 's/^/    /')"; done
          continue ;;
        knowledge:learned)
          for base in "$ROOT/.kiro/rules" "$ROOT/.claude/rules"; do
            for h in "$base"/squad-learned-*.md; do [[ -f "$h" ]] || continue; a="$(basename "$h" .md)"; a="${a#squad-learned-}"
              out="$("$CHECK" learned "$ROOT" "$a" 2>&1)" || add "check.sh learned $a failed:"$'\n'"$(printf '%s\n' "$out" | grep -E '^FAIL:' | sed 's/^/    /')"; done
          done
          continue ;;
      esac
      ev=""; [[ "$tg" == *:* ]] && ev="${tg#*:}"
      # shellcheck disable=SC2086  # $ev is empty or one word
      out="$("$CHECK" "${tg%%:*}" "$DIR" $ev 2>&1)" \
        || add "scripts/squad/check.sh ${tg%%:*} failed:"$'\n'"$(printf '%s\n' "$out" | grep -E '^FAIL:' | sed 's/^/    /')"
    done
  fi
  # error ledger: every id in "fixes:" has a fix record; a touched ledger must be well-formed
  fx="$(field fixes | grep -oE 'E-[a-z0-9-]+-[0-9]+' || true)"
  for id in $fx; do
    grep -qE "^### $id · fix · " "$DIR/records/errors.md" 2>/dev/null || add "fixes lists $id but errors.md has no '### $id · fix · <date>' record (root cause, fix, prevention)"
  done
  if [[ -n "$fx" ]] || printf '%s' "$(field artifacts)" | grep -q 'errors\.md'; then
    if [[ -x "$CHECK" && -n "$DIR" ]]; then
      out="$("$CHECK" errors "$DIR" 2>&1)" || add "scripts/squad/check.sh errors failed:"$'\n'"$(printf '%s\n' "$out" | grep -E '^FAIL:' | sed 's/^/    /')"
    fi
  fi
  # the HANDOFF verdict must match the artifact
  if [[ "$ROLE" == squad-qa && "$MODE" != plan && -n "$DIR" ]]; then
    fv="$(grep -oE '^## Verdict: *(PASS|FAIL)' "$DIR/6-verify/regression-report-$ENVV.md" 2>/dev/null | awk '{print $3}')"
    [[ "$fv" == "$(field verdict)" ]] || add "HANDOFF verdict '$(field verdict)' differs from regression-report-$ENVV.md ('$fv')"
  fi
  if [[ "$ROLE" == squad-reviewer && -n "$DIR" ]]; then
    fv="$(grep -oE '^## Verdict: *(APPROVE|CHANGES_REQUESTED)' "$DIR/6-verify/review-report.md" 2>/dev/null | awk '{print $3}')"
    [[ "$fv" == "$(field verdict)" ]] || add "HANDOFF verdict '$(field verdict)' differs from review-report.md ('$fv')"
  fi
fi

if [[ -z "$problems" ]]; then rm -f "$COUNTER"; exit 0; fi
echo $((n + 1)) > "$COUNTER"
jq -n --arg r "Not done yet (squad Definition of Done, $ROLE):"$'\n'"$problems"$'\n'"Fix these and finish again, or report status: blocked with what is missing." '{decision: "block", reason: $r}'
exit 0
