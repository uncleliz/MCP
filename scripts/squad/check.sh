#!/usr/bin/env bash
# scripts/squad/check.sh <target> <feature-dir> [env]   |   check.sh ready <stage> <feature-dir>
# Definition of Done (and Ready) for squad artifacts, as executable checks — so "done" means the same thing
# to every role, the Delivery Manager and the hooks. Prints FAIL/WARN lines, then PASS or FAIL.
# Exit 0 = pass (warnings allowed), 1 = fail, 64 = usage.
# Targets: prd research options brief requirements architecture contract plan testplan report <env>
#          review releaselog <env> cabpack decisions retro errors state
#          knowledge: lessons <knowledge-dir> | handbook <knowledge-dir> <role> | learned <repo-root> <area> | distill-log <knowledge-dir>
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64; }
[[ $# -ge 2 ]] || usage
TARGET="$1"; shift
fails=0; warns=0
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
warn() { echo "WARN: $*"; warns=$((warns + 1)); }
finish() { if [[ $fails -gt 0 ]]; then echo "FAIL ($fails failed, $warns warnings)"; exit 1; fi; echo "PASS ($warns warnings)"; exit 0; }

# ---------- helpers ----------
tier() { grep -o '"tier"[^,}]*' "$DIR/state.json" 2>/dev/null | sed 's/.*: *"\{0,1\}\([a-z]*\).*/\1/' | head -1; }
need_file() { [[ -s "$1" ]] || { fail "$(basename "$1") is missing or empty"; return 1; }; }
heads() {  # heads <file> <heading prefix>... → each must start a "## " line
  local f="$1" h; shift
  for h in "$@"; do grep -q "^## $h" "$f" || fail "$(basename "$f"): missing section '## $h' (keep template headings in English)"; done
}
section() {  # section <file> <heading prefix> → body until next "## "
  awk -v h="## $2" 'index($0, h) == 1 {on = 1; next} on && /^## / {exit} on {print}' "$1"
}
ids_in() { grep -oE "$1-[0-9]+" "$2" 2>/dev/null | LC_ALL=C sort -u; }
live_ids() {  # IDs defined in a file, excluding ~~removed~~ ones
  grep -oE "$1-[0-9]+" "$2" 2>/dev/null | LC_ALL=C sort -u | while read -r id; do
    grep -q "~~$id~~" "$2" || echo "$id"
  done
}
# error ledger of this feature → "id<TAB>sev<TAB>status<TAB>found<TAB>evidence" (status open|fixed|closed|accepted)
ledger() {
  [[ -f "$DIR/records/errors.md" ]] || return 0
  awk '
    /^## E-[a-z0-9-]+-[0-9]+ · S[1-4] · / {split($0, h, " · "); id = h[1]; sub(/^## /, "", id); order[++n] = id; SV[id] = h[2]; m = "open"; cur = id; next}
    /^### E-[a-z0-9-]+-[0-9]+ · (fix|verified|accepted) · / {split($0, h, " · "); r = h[1]; sub(/^### /, "", r); m = h[2]; cur = ""
      if (m == "verified") ST[r] = "closed"; else if (m == "accepted") ST[r] = "accepted"; else if (ST[r] == "") ST[r] = "fixed"; next}
    m == "open" && cur != "" && /^- Found:/ {f = $0; sub(/^- Found: */, "", f); sub(/ ·.*/, "", f); FO[cur] = f}
    m == "open" && cur != "" && /^- Evidence:/ {e = $0; sub(/^- Evidence: */, "", e); EV[cur] = e}
    END {for (k = 1; k <= n; k++) {x = order[k]; printf "%s\t%s\t%s\t%s\t%s\n", x, SV[x], (ST[x] == "" ? "open" : ST[x]), FO[x], EV[x]}}
  ' "$DIR/records/errors.md"
}
open_severe() { ledger | awk -F'\t' '($3 == "open" || $3 == "fixed") && ($2 == "S1" || $2 == "S2") {print $1 " " $2 " " $3}'; }

VAGUE='(\bfast\b|\bquick(ly)?\b|user[- ]friendly|\beasy\b|\beasily\b|\brobust\b|\bscalable\b|\befficient(ly)?\b|\bappropriate(ly)?\b|\bas needed\b|\betc\.?\b|\bseamless(ly)?\b|\bintuitive\b|\bnhanh\b|dễ dùng|thân thiện|hợp lý|tối ưu|phù hợp|linh hoạt|mượt)'
vague_lines() { grep -nEi "$VAGUE" | head -5; }

# ---------- ready <stage> ----------
if [[ "$TARGET" == ready ]]; then
  STAGE="${1:-}"; DIR="${2:-}"; [[ -n "$STAGE" && -d "$DIR" ]] || usage
  req=""
  case "$STAGE" in
    frame) req="" ;;
    research) req="1-discovery/product-requirement.md" ;;
    options) req="1-discovery/product-requirement.md 1-discovery/market-research.md" ;;
    brief) req="1-discovery/product-requirement.md 1-discovery/market-research.md 1-discovery/options.md" ;;
    cto-plan-review) req="1-discovery/product-requirement.md 1-discovery/market-research.md 1-discovery/options.md 1-discovery/decision-brief.md" ;;
    finalize|ba) req="2-gate1/plan-approval.md 1-discovery/product-requirement.md" ;;
    sa) req="2-gate1/plan-approval.md 3-spec/requirements.md" ;;
    lead) req="3-spec/requirements.md 4-design/architecture.md 4-design/api-contract.yaml" ;;
    cto-design) req="3-spec/requirements.md 4-design/architecture.md 4-design/api-contract.yaml 5-plan/implementation-plan.md" ;;
    qa-plan) req="3-spec/requirements.md 4-design/architecture.md 4-design/api-contract.yaml 5-plan/implementation-plan.md" ;;
    backend|frontend) req="5-plan/implementation-plan.md 4-design/api-contract.yaml 6-verify/test-plan.md 6-verify/test-cases.md" ;;
    qa-dev) req="6-verify/test-cases.md" ;;
    review) req="6-verify/regression-report-dev.md" ;;
    deploy-uat) req="6-verify/review-report.md" ;;
    qa-uat) req="7-release/release-log.md" ;;
    cto-promote) req="6-verify/regression-report-uat.md 6-verify/review-report.md 7-release/release-log.md" ;;
    deploy-pre) req="6-verify/review-report.md" ;;
    qa-pre) req="7-release/release-log.md" ;;
    cto-cab) req="6-verify/regression-report-pre.md 6-verify/review-report.md 7-release/release-log.md" ;;
    retro) req="records/decisions.md" ;;
    cab-pack) req="6-verify/regression-report-pre.md records/decisions.md 7-release/release-log.md" ;;
    ceo-golive|deploy-prod) req="7-release/cab-pack.md 8-gate2/cab-approval.md" ;;
    *) echo "unknown stage '$STAGE' (no readiness rule)"; exit 0 ;;
  esac
  for f in $req; do [[ -s "$DIR/$f" ]] || fail "input $f is missing"; done
  case "$STAGE" in
    deploy-uat|deploy-pre|deploy-prod)
      co="$(dirname "$0")/coord.sh"
      if [[ -x "$co" ]] && git rev-parse --git-dir >/dev/null 2>&1; then
        e="${STAGE#deploy-}"; h="$("$co" holder "env-$e")"
        [[ "$h" == "$(basename "$DIR")" ]] || fail "env-$e lock is held by '${h:-nobody}', not this feature — scripts/squad/coord.sh lock env-$e $(basename "$DIR")"
      fi ;;
  esac
  case "$STAGE" in
    cto-cab|cab-pack|ceo-golive|deploy-prod)
      o="$(open_severe)"; [[ -z "$o" ]] || fail "open S1/S2 defects in errors.md must be verified before $STAGE: $(printf '%s' "$o" | tr '\n' ';')" ;;
  esac
  if [[ -f "$DIR/state.json" ]]; then
    for f in $req; do b="$(basename "$f")"; grep -q "\"stale\"[^]]*\"${b%%.*}" "$DIR/state.json" && fail "input $f is marked stale in state.json"; done
  fi
  finish
fi

DIR="$1"; ENV="${2:-}"
[[ -d "$DIR" ]] || { echo "no feature dir $DIR" >&2; exit 64; }
TIER="$(tier)"; TIER="${TIER:-large}"

case "$TARGET" in
  prd)
    f="$DIR/1-discovery/product-requirement.md"; need_file "$f" || finish
    heads "$f" "Problem" "Users & personas" "Goals / Non-goals" "Success metrics" "Measurement plan" "Scope" "User journeys" "Assumptions & risks" "Open questions"
    section "$f" "Success metrics" | grep -q '[0-9]' || fail "Success metrics: no number — every metric needs a target value and a time window"
    section "$f" "Scope" | grep -qi 'must' || fail "Scope: no Must items"
    section "$f" "Scope" | grep -qiE "won'?t" || warn "Scope: no Won't items — list what is explicitly out"
    v="$(section "$f" "Success metrics" | vague_lines)"; [[ -n "$v" ]] && warn "Success metrics: vague wording: $v"
    ;;
  research)
    f="$DIR/1-discovery/market-research.md"; need_file "$f" || finish
    heads "$f" "Question" "Internal assets" "Landscape" "Shortlist" "Key constraints" "Sources" "Confidence & gaps"
    [[ "$TIER" == large ]] && heads "$f" "Build-vs-buy"
    n="$(section "$f" "Sources" | grep -cE '^[[:space:]]*(\[[0-9]+\]|[0-9]+\.)')"
    min=3; [[ "$TIER" == standard ]] && min=2
    [[ "$n" -ge $min ]] || fail "Sources: $n numbered sources, need ≥ $min"
    c="$(grep -oE '\[[0-9]+\]' "$f" | LC_ALL=C sort -u | wc -l | tr -d ' ')"
    [[ "$c" -ge 1 ]] || fail "no [n] citations in the body — every factual claim cites a source"
    section "$f" "Sources" | grep -qE '20[0-9]{2}-[0-9]{2}' || warn "Sources: no access dates (YYYY-MM)"
    ;;
  options)
    f="$DIR/1-discovery/options.md"; need_file "$f" || finish
    n="$(grep -cE '^## Option [A-Z0-9]' "$f")"
    min=3; [[ "$TIER" == standard ]] && min=2
    [[ "$n" -ge $min ]] || fail "$n options ('## Option X — name'), need ≥ $min for tier $TIER"
    heads "$f" "Baseline" "Scoring" "Recommendation" "Sensitivity"
    section "$f" "Scoring" | grep -q '|' || fail "Scoring: no score table"
    grep -qiE 'as of 20[0-9]{2}' "$f" || warn "no 'as of <date>' on costs"
    ;;
  brief)
    f="$DIR/1-discovery/decision-brief.md"; need_file "$f" || finish
    heads "$f" "TL;DR" "Problem & goal" "What we found" "Inheritance & platform deviation" "Options" "Recommendation" "Plan" "Go-live criteria" "Budget & escalation" "Track" "Risks" "Decisions needed from the CEO"
    max=7000; [[ "$TIER" == standard ]] && max=3500
    chars="$(wc -m < "$f" | tr -d ' ')"
    [[ "$chars" -le $max ]] || fail "decision-brief.md is $chars characters; limit $max for tier $TIER (≈ $([[ $max -eq 3500 ]] && echo 1 || echo 2) page) — cut"
    tl="$(section "$f" "TL;DR" | grep -cv '^[[:space:]]*$')"; [[ "$tl" -le 4 ]] || fail "TL;DR has $tl lines, max 3"
    section "$f" "Go-live criteria" | grep -q '[0-9]' || fail "Go-live criteria: nothing measurable (no numbers)"
    ;;
  requirements)
    f="$DIR/3-spec/requirements.md"; need_file "$f" || finish
    heads "$f" "Glossary" "Existing behaviour" "Business rules" "Functional requirements" "Non-functional requirements" "Data & privacy" "Out of scope" "Traceability"
    frs="$(grep -oE '^### FR-[0-9]+' "$f" | sed 's/### //')"
    [[ -n "$frs" ]] || fail "no '### FR-nnn' requirements"
    for fr in $frs; do
      grep -q "~~$fr~~" "$f" && continue
      body="$(awk -v h="### $fr" 'index($0, h) == 1 {on = 1; next} on && /^##/ {exit} on {print}' "$f")"
      printf '%s' "$body" | grep -qE 'Priority: *(Must|Should|Could)' || fail "$fr: no 'Priority: Must|Should|Could'"
      na="$(printf '%s\n' "$body" | grep -cE 'AC-[0-9]+')"
      [[ "$na" -ge 2 ]] || fail "$fr: $na acceptance criteria, need ≥ 2 (happy path + negative)"
      printf '%s\n' "$body" | grep -E 'AC-[0-9]+' | grep -viE 'given.*when.*then' | head -3 | while read -r l; do echo "FAIL: $fr: AC not in Given/When/Then form: $l"; done
      printf '%s\n' "$body" | grep -E 'AC-[0-9]+' | grep -viE 'given.*when.*then' | grep -q . && fails=$((fails + 1))
      v="$(printf '%s\n' "$body" | grep -E 'AC-[0-9]+' | vague_lines)"; [[ -n "$v" ]] && warn "$fr: vague wording in AC: $v"
    done
    section "$f" "Non-functional requirements" | grep -E 'NFR-[0-9]+' | grep -vE '[0-9].*(ms|s|%|req|rps|users|MB|GB|min|h|day|AA|WCAG)|[0-9]{2,}|not applicable|n/a' | head -5 | while read -r l; do
      echo "WARN: NFR without a measurable threshold: $l"; done
    section "$f" "Traceability" | grep -q 'FR-' || fail "Traceability: no Must → FR mapping"
    ;;
  architecture)
    f="$DIR/4-design/architecture.md"; need_file "$f" || finish
    heads "$f" "Context & constraints" "Tech stack decision" "Component view" "Data model" "Key flows" "Cross-cutting" "NFR mapping" "Security & threat model" "Observability" "Rollout strategy" "Run & test commands" "Environments & deployment" "Cost model" "ADRs" "Design review" "Risks & spikes"
    section "$f" "Observability" | grep -qiE 'health' || fail "Observability: no health/ready check"
    section "$f" "Run & test commands" | grep -qE '`[^`]+`|^    |^```' || fail "Run & test commands: no concrete command"
    section "$f" "Run & test commands" | grep -qE 'SQUAD_PORT_OFFSET|COMPOSE_PROJECT_NAME' || warn "Run & test commands ignore SQUAD_PORT_OFFSET / COMPOSE_PROJECT_NAME — parallel features would collide"
    if [[ -f "$DIR/3-spec/requirements.md" ]]; then
      for fr in $(live_ids FR "$DIR/3-spec/requirements.md"); do
        grep -q "$fr" "$f" "$DIR/4-design/api-contract.yaml" 2>/dev/null || fail "$fr is not covered by architecture.md or api-contract.yaml"
      done
    fi
    ;;
  contract)
    f="$DIR/4-design/api-contract.yaml"; need_file "$f" || finish
    if head -5 "$f" | grep -qi 'no HTTP surface'; then echo "contract: non-HTTP interface declared"; finish; fi
    grep -qE '^openapi: *["'"'"']?3\.1' "$f" || fail "not OpenAPI 3.1 ('openapi: 3.1.x')"
    ops="$(grep -cE '^[[:space:]]+operationId:' "$f")"; xr="$(grep -cE '^[[:space:]]+x-requirements:' "$f")"
    [[ "$ops" -ge 1 ]] || fail "no operations (operationId)"
    [[ "$xr" -ge "$ops" ]] || fail "$ops operations but only $xr x-requirements — every operation lists its FR ids"
    grep -qE '^[[:space:]]+Error:' "$f" || fail "no shared 'Error' schema under components/schemas"
    grep -qE '^[[:space:]]+example(s)?:' "$f" || warn "no examples — frontend mocks are generated from them"
    ;;
  plan)
    f="$DIR/5-plan/implementation-plan.md"; need_file "$f" || finish
    heads "$f" "Summary & sequencing" "Patterns to mirror" "Setup tasks" "Tasks" "Parallelism" "Milestones vs baseline" "Release path" "Definition of Done" "Risks"
    grep -qE '^\| *T-[0-9]+ *\|' "$f" || fail "no task rows ('| T-nnn | Owner | …')"
    grep -E '^\| *T-[0-9]+ *\|' "$f" | awk -F'|' '{o=$3; gsub(/ /,"",o); if (o !~ /^(BE|FE|QA|OPS)$/) print "FAIL: task" $2 "has owner \"" o "\" (BE|FE|QA|OPS)"}'
    bad="$(grep -E '^\| *T-[0-9]+ *\|' "$f" | awk -F'|' '{o=$3; gsub(/ /,"",o); if (o !~ /^(BE|FE|QA|OPS)$/) n++} END {print n+0}')"; fails=$((fails + bad))
    if [[ -f "$DIR/3-spec/requirements.md" ]]; then
      for ac in $(live_ids AC "$DIR/3-spec/requirements.md"); do grep -qw "$ac" "$f" || fail "$ac is not covered by any task"; done
    fi
    section "$f" "Milestones vs baseline" | grep -q '%' || fail "Milestones vs baseline: no delta %"
    ;;
  testplan)
    f="$DIR/6-verify/test-plan.md"; need_file "$f" || true
    [[ -f "$f" ]] && heads "$f" "Scope" "Test levels" "Environments & test data" "Entry / exit criteria" "Risk-based focus" "Non-functional tests" "Security smoke"
    t="$DIR/6-verify/test-cases.md"; need_file "$t" || finish
    grep -qE '^\| *TC-[0-9]+' "$t" || fail "test-cases.md: no '| TC-nnn |' rows"
    grep -E '^\| *TC-[0-9]+' "$t" | awk -F'|' '{l=$4; p=$5; gsub(/ /,"",l); gsub(/ /,"",p); if (l !~ /^(unit|integration|contract|component|E2E)$/ || p !~ /^P[123]$/) print "FAIL: " $2 "has Level \"" l "\" / Priority \"" p "\" (unit|integration|contract|component|E2E, P1-P3)"}'
    fails=$((fails + $(grep -E '^\| *TC-[0-9]+' "$t" | awk -F'|' '{l=$4; p=$5; gsub(/ /,"",l); gsub(/ /,"",p); if (l !~ /^(unit|integration|contract|component|E2E)$/ || p !~ /^P[123]$/) n++} END {print n+0}')))
    if [[ -f "$DIR/3-spec/requirements.md" ]]; then
      for ac in $(live_ids AC "$DIR/3-spec/requirements.md"); do grep -qw "$ac" "$t" || fail "$ac has no test case"; done
      # every Must FR has at least one E2E TC covering one of its ACs
      for fr in $(grep -oE '^### FR-[0-9]+' "$DIR/3-spec/requirements.md" | sed 's/### //'); do
        body="$(awk -v h="### $fr" 'index($0, h) == 1 {on = 1; next} on && /^##/ {exit} on {print}' "$DIR/3-spec/requirements.md")"
        printf '%s' "$body" | grep -qE 'Priority: *Must' || continue
        grep -q "~~$fr~~" "$DIR/3-spec/requirements.md" && continue
        acs="$(printf '%s\n' "$body" | grep -oE 'AC-[0-9]+' | LC_ALL=C sort -u)"; hit=0
        for ac in $acs; do grep -E '^\| *TC-[0-9]+' "$t" | grep -w "$ac" | grep -q '| *E2E *|' && hit=1; done
        [[ $hit -eq 1 ]] || fail "Must $fr has no E2E test case"
      done
    fi
    ;;
  report)
    [[ "$ENV" =~ ^(dev|uat|pre)$ ]] || { echo "report needs env dev|uat|pre" >&2; exit 64; }
    f="$DIR/6-verify/regression-report-$ENV.md"; need_file "$f" || finish
    heads "$f" "Summary" "Results" "Failures triage" "Verdict"
    v="$(grep -oE '^## Verdict: *(PASS|FAIL)' "$f" | awk '{print $3}')"
    [[ -n "$v" ]] || fail "no '## Verdict: PASS' or '## Verdict: FAIL'"
    grep -qE '^\| *TC-[0-9]+' "$f" || fail "Results: no TC rows"
    if [[ "$v" == PASS ]] && grep -E '^\| *TC-[0-9]+' "$f" | grep -qiE '\| *fail(ed)? *\|'; then fail "verdict PASS but a TC row is failed"; fi
    tri="$(section "$f" "Failures triage" | grep -E '^\| *(TC|EXP)-' | awk -F'|' '{c=$(NF-1); gsub(/ /,"",c); if (c !~ /^(backend|frontend|contract|spec|test-flaky)$/) print $2 "class \"" c "\""}')"
    [[ -n "$tri" ]] && fail "triage class must be backend|frontend|contract|spec|test-flaky: $(printf '%s' "$tri" | tr '\n' ';')"
    if [[ "$v" == FAIL ]]; then  # every product failure is in the error ledger
      for tc in $(section "$f" "Failures triage" | grep -E '^\| *(TC|EXP)-' | grep -v 'test-flaky' | grep -oE '(TC|EXP)-[0-9]+' | LC_ALL=C sort -u); do
        grep -qw "$tc" "$DIR/records/errors.md" 2>/dev/null || fail "$tc failed but is not in errors.md — open an E- entry (squad-errors)"
      done
    else  # a PASS re-run closes what QA found in this env
      ledger | awk -F'\t' -v e="qa-$ENV" '$4 == e && ($3 == "open" || $3 == "fixed") {print $1}' | while read -r id; do echo "FAIL: $id (found in qa-$ENV) passes now — append a '### $id · verified' record"; done
      fails=$((fails + $(ledger | awk -F'\t' -v e="qa-$ENV" '$4 == e && ($3 == "open" || $3 == "fixed")' | wc -l | tr -d ' ')))
    fi
    [[ "$ENV" == dev ]] && { section "$f" "Summary" | grep -qiE 'coverage.*[0-9]+ *%' || fail "Summary: no coverage % for dev"; }
    [[ "$ENV" == pre ]] && { section "$f" "Summary" | grep -qi 'NFR' || fail "Summary: no measured NFR values for pre"; }
    grep -E '^\| *TC-[0-9]+' "$f" | grep -iE '\| *pass(ed)? *\|' | grep -vqE '(/|\.log|\.png|\.txt|\.json|\.html|#)' && warn "some passed TCs have no evidence path"
    ;;
  review)
    f="$DIR/6-verify/review-report.md"; need_file "$f" || finish
    v="$(grep -oE '^## Verdict: *(APPROVE|CHANGES_REQUESTED)' "$f" | awk '{print $3}')"
    [[ -n "$v" ]] || fail "no '## Verdict: APPROVE' or '## Verdict: CHANGES_REQUESTED'"
    open="$(grep -E '^\| *R-[0-9]+ *\| *(CRITICAL|HIGH) *\|' "$f" | grep -viE 'fixed|resolved|closed' | wc -l | tr -d ' ')"
    if [[ "$v" == APPROVE && "$open" -gt 0 ]]; then fail "verdict APPROVE with $open open CRITICAL/HIGH finding(s)"; fi
    if [[ "$v" == CHANGES_REQUESTED && "$open" -eq 0 ]]; then fail "verdict CHANGES_REQUESTED but no open CRITICAL/HIGH finding"; fi
    for r in $(grep -E '^\| *R-[0-9]+ *\| *(CRITICAL|HIGH) *\|' "$f" | grep -oE '^\| *R-[0-9]+' | grep -oE 'R-[0-9]+'); do
      grep -qw "$r" "$DIR/records/errors.md" 2>/dev/null || fail "$r is CRITICAL/HIGH but not in errors.md — open an E- entry (squad-errors)"
    done
    if [[ "$v" == APPROVE ]]; then
      n="$(ledger | awk -F'\t' '$4 == "review" && ($3 == "open" || $3 == "fixed")' | wc -l | tr -d ' ')"
      [[ "$n" -eq 0 ]] || fail "$n review defect(s) still open in errors.md — append '### E-… · verified' for the fixed ones"
    fi
    grep -E '^\| *R-[0-9]+' "$f" | awk -F'|' 'NF < 9 {print "WARN: finding row with missing columns:" $2}'
    ;;
  releaselog)
    f="$DIR/7-release/release-log.md"; need_file "$f" || finish
    [[ "$ENV" =~ ^(uat|pre|prod)$ ]] || { echo "releaselog needs env uat|pre|prod" >&2; exit 64; }
    entry="$(awk -v e=" · $ENV · " '/^## / {on = (index($0, e) > 0)} on {print}' "$f")"
    [[ -n "$entry" ]] || { fail "no '## <datetime> · $ENV · <mode> · <version>' entry"; finish; }
    printf '%s' "$entry" | grep -qiE 'Smoke: *(pass|fail)' || fail "$ENV entry: no 'Smoke: pass|fail'"
    printf '%s' "$entry" | grep -qE 'Commands:' || fail "$ENV entry: no 'Commands:' with the exact script calls"
    if printf '%s' "$entry" | grep -qiE 'Smoke: *fail|Rollback: *executed'; then
      ledger | awk -F'\t' -v e="$ENV" 'index($4, e) > 0 || ($4 == "watch" && e == "prod")' | grep -q . \
        || fail "$ENV smoke failed or rolled back but errors.md has no entry found in deploy-$ENV/watch (squad-errors)"
    fi
    if [[ "$ENV" == pre ]]; then
      printf '%s' "$entry" | grep -qiE 'rehearsed in *[0-9]' || fail "pre entry: no 'Rollback: rehearsed in <seconds>'"
      printf '%s' "$entry" | grep -qiE 'SLI read-out: *[^ ]' || fail "pre entry: no 'SLI read-out:' sample"
    fi
    ;;
  cabpack)
    f="$DIR/7-release/cab-pack.md"; need_file "$f" || finish
    grep -qE 'CHG-[a-z0-9-]+-[0-9]{8}' "$f" || fail "no Change ID 'CHG-<feature>-<yyyymmdd>'"
    grep -qiE 'Risk rating *\| *(low|medium|high)' "$f" || fail "no Risk rating low|medium|high"
    grep -qE 'READY_FOR_CAB.*D-[0-9]+' "$f" || fail "no 'READY_FOR_CAB — decisions.md D-nnn'"
    for s in "1\." "2\." "3\." "4\." "5\." "6\." "7\." "8\." "9\."; do grep -qE "^## $s" "$f" || fail "missing section ## ${s%\\.}."; done
    grep -iE '^## 6\.' -A6 "$f" | grep -qE '[0-9]+ *(s|sec|seconds|min)' || fail "Rollback plan: no rehearsed time-to-rollback"
    for id in $(ledger | awk -F'\t' '$3 != "closed" {print $1}'); do grep -qw "$id" "$f" || fail "$id is not closed — list it under Residual risks"; done
    o="$(open_severe)"; [[ -z "$o" ]] || fail "open S1/S2 defects: $(printf '%s' "$o" | tr '\n' ';')"
    ;;
  decisions)
    f="$DIR/records/decisions.md"; need_file "$f" || finish
    last="$(awk '/^## D-[0-9]+/ {buf = ""} {buf = buf $0 "\n"} END {printf "%s", buf}' "$f")"
    printf '%s' "$last" | grep -qE '^## D-[0-9]+ · [a-z-]+ · ' || fail "last entry heading is not '## D-nnn · <mode> · <datetime>'"
    printf '%s' "$last" | grep -qE 'Decision: *(APPROVE|APPROVE_FOR_CEO|PROMOTE|READY_FOR_CAB|RETURN|ESCALATE|ACK)\b' || fail "last entry: Decision not one of APPROVE|APPROVE_FOR_CEO|PROMOTE|READY_FOR_CAB|RETURN|ESCALATE|ACK"
    ev="$(printf '%s' "$last" | grep -E 'Evidence:' | sed 's/.*Evidence: *//')"
    printf '%s' "$ev" | grep -qE '([A-Z]{1,3}-[0-9]+|\.md|\.yaml|#)' || fail "last entry: Evidence cites no file, section or ID"
    if printf '%s' "$last" | grep -qE '· (promote|cab-readiness) ·'; then
      printf '%s' "$last" | grep -qiE 'santa|checker' || fail "promote/cab-readiness entry does not record the independent checkers' result"
    fi
    printf '%s' "$last" | grep -qE 'Decision: *RETURN' && { printf '%s' "$last" | grep -qE 'Returned to: *[a-z]' || fail "RETURN without 'Returned to: <stage + IDs>'"; }
    printf '%s' "$last" | grep -qE 'Decision: *ESCALATE' && { printf '%s' "$last" | grep -qE 'Escalation: *.+' || fail "ESCALATE without 'Escalation: <rule, options, recommendation>'"; }
    ;;
  retro)
    f="$DIR/9-retro/retro.md"; need_file "$f" || finish
    heads "$f" "Outcome" "Flow metrics" "Token use" "Quality metrics" "Defects" "What worked" "What hurt" "Lessons"
    for id in $(ledger | awk -F'\t' '$2 == "S1" || $2 == "S2" {print $1}'; grep -E '^- Recurrence of: *E-' "$DIR/records/errors.md" 2>/dev/null | grep -oE 'E-[a-z0-9-]+-[0-9]+'); do
      grep -qw -- "$id" "$f" || fail "retro.md does not root-cause $id (every S1/S2 and every recurrence)"
    done
    ;;
  lessons)
    f="$DIR/lessons.md"; [[ -f "$f" ]] || f="$DIR/../../knowledge/lessons.md"; need_file "$f" || finish
    ROLES_RE='(squad-(po|researcher|sa|ba|lead|qa|backend|frontend|reviewer|release|cto)|all)'
    bad="$(grep -E '^##+ L-' "$f" | grep -vE "^## L-[0-9]{3} · $ROLES_RE · [a-z0-9-]+ · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$|^### L-[0-9]{3} · (retired|promoted) · K-[0-9]{3} · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$")"
    [[ -z "$bad" ]] || fail "bad lesson record heading(s) (expected '## L-nnn · <squad-role|all> · <feature|k-nnn> · YYYY-MM-DD' or '### L-nnn · retired|promoted · K-nnn · YYYY-MM-DD'): $(printf '%s' "$bad" | head -3 | tr '\n' ';')"
    out="$(awk '
      function close_rec() { if (t == "lesson") { if (!rule) print "FAIL: " id " has no \"- Rule:\""; if (!why) print "FAIL: " id " has no \"- Why:\" with evidence" }
                             if (t == "retired" && !reason) print "FAIL: " id " (retired) has no \"- Reason:\""
                             if (t == "promoted" && !to) print "FAIL: " id " (promoted) has no \"- To:\" (learned rule, check or hook)" }
      /^## L-/ { close_rec(); split($0, h, " · "); id = h[1]; sub(/^## /, "", id); t = "lesson"; rule = why = 0; seen[id] = 1; next }
      /^### L-/ { close_rec(); split($0, h, " · "); id = h[1]; sub(/^### /, "", id); t = h[2]; reason = to = 0; if (!(id in seen)) print "FAIL: " t " record for " id " which was never written"; next }
      /^- Rule: *[^ ]/ { rule = 1 }
      /^- Why: *.*([A-Z]{1,3}-[0-9]+|E-[a-z0-9-]+-[0-9]+)/ { why = 1 }
      /^- Reason: *[^ ]/ { reason = 1 }
      /^- To: *[^ ]/ { to = 1 }
      /^- Supersedes:/ { m = split($0, a, /[ ,]+/); for (k = 1; k <= m; k++) if (a[k] ~ /^L-[0-9]+$/ && !(a[k] in seen)) print "FAIL: " id " supersedes unknown " a[k] }
      END { close_rec() }' "$f")"
    if [[ -n "$out" ]]; then printf '%s\n' "$out"; fails=$((fails + $(printf '%s\n' "$out" | grep -c '^FAIL:'))); fi
    ;;
  platform-baseline)
    f="$DIR/platform-baseline.md"; [[ -f "$f" ]] || f="$DIR/../../knowledge/platform-baseline.md"; need_file "$f" || finish
    heads "$f" "Approved languages & frameworks" "Approved datastores" "Approved infrastructure & deployment" "Approved architecture patterns" "Approved vendors & paid services" "Data-boundary rules" "Change log"
    ;;
  business-baseline)
    f="$DIR/business-baseline.md"; [[ -f "$f" ]] || f="$DIR/../../knowledge/business-baseline.md"; need_file "$f" || finish
    heads "$f" "Capabilities" "Domain entities" "Main user journeys" "Business invariants" "Constraints & boundaries" "Change log"
    grep -qE '^- *INV-[0-9]' "$f" || warn "Business invariants: no INV-nnn rule yet — list the rules a new feature must not break"
    ;;
  handbook)
    role="${2:-}"; f="$DIR/roles/$role.md"; need_file "$f" || finish
    max="$(grep '^HANDBOOK_MAX_LINES=' "$(git rev-parse --show-toplevel 2>/dev/null || echo .)/.kiro/squad/config.env" 2>/dev/null | cut -d= -f2 || true)"; [[ -n "$max" ]] || max="$(grep '^HANDBOOK_MAX_LINES=' "$(git rev-parse --show-toplevel 2>/dev/null || echo .)/.claude/squad/config.env" 2>/dev/null | cut -d= -f2)"; max="${max:-40}"
    head -1 "$f" | grep -qE "^# Handbook · $role · K-[0-9]{3} · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$" || fail "first line must be '# Handbook · $role · K-nnn · YYYY-MM-DD'"
    n="$(grep -c '^- ' "$f")"; [[ "$n" -le "$max" ]] || fail "$n rules, limit $max — compress further (merge, promote to learned rules or checks)"
    grep '^- ' "$f" | grep -vE '\[(L-[0-9]+|E-[a-z0-9-]+-[0-9]+|D-[0-9]+|ADR-[0-9]+)([, ]+(L-[0-9]+|E-[a-z0-9-]+-[0-9]+|D-[0-9]+|ADR-[0-9]+))*\]' | head -3 | while read -r l; do echo "FAIL: rule without a [source id]: $l"; done
    fails=$((fails + $(grep '^- ' "$f" | grep -cvE '\[(L-[0-9]+|E-[a-z0-9-]+-[0-9]+|D-[0-9]+|ADR-[0-9]+)([, ]+(L-[0-9]+|E-[a-z0-9-]+-[0-9]+|D-[0-9]+|ADR-[0-9]+))*\]' || true)))
    if [[ -x "$(dirname "$0")/knowledge.sh" ]]; then
      act="$(SQUAD_ROOT="$(cd "$DIR/../../.." && pwd)" "$(dirname "$0")/knowledge.sh" active | cut -f1)"
      for l in $(grep -oE 'L-[0-9]+' "$f" | sort -u); do printf '%s\n' "$act" | grep -qx "$l" || fail "cites $l which is not an active lesson (superseded, retired or promoted)"; done
    fi
    grep -vE '^(# |> |- |$)' "$f" | head -1 | grep -q . && warn "only the title, one '> ' note and '- ' rules belong in a handbook"
    ;;
  learned)
    area="${2:-}"; f="$DIR/.claude/rules/squad-learned-$area.md"; need_file "$f" || finish
    head -1 "$f" | grep -qx -- '---' && awk 'NR > 1 && /^---$/ {exit} NR > 1' "$f" | grep -qE '^paths:' || fail "needs frontmatter with paths: (a learned rule loads only for its code area)"
    grep -qE "^# Learned · $area · K-[0-9]{3}\$" "$f" || fail "needs the heading '# Learned · $area · K-nnn'"
    n="$(grep -c '^- ' "$f")"; [[ "$n" -ge 1 && "$n" -le 15 ]] || fail "$n rules — a learned rule file holds 1–15 rules"
    grep '^- ' "$f" | grep -vqE '\[(L-[0-9]+|E-[a-z0-9-]+-[0-9]+|D-[0-9]+|ADR-[0-9]+)' && fail "every rule cites its source id in [ ]"
    ;;
  distill-log)
    f="$DIR/distill-log.md"; need_file "$f" || finish
    grep -E '^## K-' "$f" | grep -vqE '^## K-[0-9]{3} · 20[0-9]{2}-[0-9]{2}-[0-9]{2}$' && fail "distill headings must be '## K-nnn · YYYY-MM-DD'"
    last="$(awk '/^## K-/ {buf = ""} {buf = buf $0 "\n"} END {printf "%s", buf}' "$f")"
    for k in Features Input Merged Promoted Retired KIT Handbooks "Active lessons"; do
      printf '%s' "$last" | grep -qE "^- $k: *[^ ]" || fail "last distill entry lacks '- $k: …' (write 'none' when empty)"
    done
    ;;
  errors)
    f="$DIR/records/errors.md"; [[ -f "$f" ]] || { echo "no errors.md — no defects recorded"; finish; }
    slug="$(basename "$DIR")"
    grep -E '^##+ E-' "$f" | grep -vE "^## E-$slug-[0-9]{3} · S[1-4] · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$|^### E-$slug-[0-9]{3} · (fix|verified|accepted) · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$" | head -5 | while read -r l; do
      echo "FAIL: bad record heading (expected '## E-$slug-nnn · S1-4 · YYYY-MM-DD' or '### E-$slug-nnn · fix|verified|accepted · YYYY-MM-DD'): $l"; done
    fails=$((fails + $(grep -E '^##+ E-' "$f" | grep -cvE "^## E-$slug-[0-9]{3} · S[1-4] · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$|^### E-$slug-[0-9]{3} · (fix|verified|accepted) · 20[0-9]{2}-[0-9]{2}-[0-9]{2}\$")))
    dup="$(grep -oE '^## E-[a-z0-9-]+-[0-9]+' "$f" | sort | uniq -d)"; [[ -z "$dup" ]] || fail "duplicate open records: $dup"
    out="$(awk -v cats=" requirement design contract code test data security config deploy estimate process " '
      function check() {
        if (t == "open") { split("Category Found Introduced Escaped Symptom Evidence Owner_of_fix Recurrence_of", k, " ")
          for (i in k) { key = k[i]; gsub(/_/, " ", key); if (!(key in got)) print "FAIL: " id " (open) lacks \"- " key ":\"" }
          if ("Category" in got && index(cats, " " got["Category"] " ") == 0) print "FAIL: " id " category \"" got["Category"] "\" not in" cats }
        if (t == "fix") { split("By|Root cause|Fix|Prevention", k, "|"); for (i in k) if (!(k[i] in got) || got[k[i]] == "") print "FAIL: " id " (fix) lacks \"- " k[i] ":\"" }
        if (t == "verified") { if (!("Evidence" in got) || got["Evidence"] == "") print "FAIL: " id " (verified) lacks \"- Evidence:\"" }
        if (t == "accepted") { if (got["Reason"] !~ /D-[0-9]+/) print "FAIL: " id " (accepted) needs \"- Reason: … (D-nnn)\"" }
        if (t != "open" && t != "" && !(id in opened)) print "FAIL: " t " record for " id " which was never opened"
        delete got
      }
      /^## E-/ { check(); split($0, h, " · "); id = h[1]; sub(/^## /, "", id); t = "open"; opened[id] = 1; next }
      /^### E-/ { check(); split($0, h, " · "); id = h[1]; sub(/^### /, "", id); t = h[2]; next }
      /^- [A-Z][A-Za-z ]+:/ { kk = $0; sub(/^- /, "", kk); v = kk; sub(/:.*/, "", kk); sub(/^[^:]*: */, "", v); sub(/ ·.*/, "", v); got[kk] = v }
      END { check() }' "$f")"
    if [[ -n "$out" ]]; then printf '%s\n' "$out"; fails=$((fails + $(printf '%s\n' "$out" | grep -c '^FAIL:'))); fi
    ;;
  state)
    f="$DIR/state.json"; need_file "$f" || finish
    if command -v jq >/dev/null 2>&1; then
      jq -e . "$f" >/dev/null 2>&1 || { fail "state.json is not valid JSON"; finish; }
      for k in feature stage tier loops gates env history; do jq -e "has(\"$k\")" "$f" >/dev/null || fail "state.json: missing key '$k'"; done
      jq -e '.tier | IN("standard","large")' "$f" >/dev/null || fail "state.json: tier must be standard|large"
      jq -e '.history | type == "array" and length > 0' "$f" >/dev/null || fail "state.json: history must be a non-empty array"
    else warn "jq not installed — state.json only checked for presence"; fi
    ;;
  *) usage ;;
esac
finish
