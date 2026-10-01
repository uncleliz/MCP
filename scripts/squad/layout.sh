#!/usr/bin/env bash
# scripts/squad/layout.sh — the ONE table that says where every squad document lives and who owns it.
# check.sh, errors.sh, knowledge.sh, the hooks and the generated docs all read it; nothing else hard-codes paths.
#   layout.sh path <key> [slug] [env|role|area]   repo-relative path of a document
#   layout.sh feature-dir <slug>                   docs/squad/features/<slug>
#   layout.sh classify <path>                      key owner kind check-target slug extra  (TAB-separated) | UNKNOWN reason
#   layout.sh check                                every file under docs/squad (+ learned rules) is in the layout; exit 1 if not
#   layout.sh legacy                               list documents still in the pre-2.2 flat layout; exit 1 if any
#   layout.sh index                                regenerate docs/squad/README.md (never edit it by hand)
#   layout.sh table                                the layout as a markdown table
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
ROOT="${SQUAD_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
SQ="docs/squad"
STAGES="frame research options brief finalize ba sa lead qa-plan backend frontend qa-dev review deploy-uat qa-uat deploy-pre qa-pre cab-pack deploy-prod watch incident retro legacy"
ROLES="squad-po squad-researcher squad-sa squad-ba squad-lead squad-qa squad-backend squad-frontend squad-reviewer squad-release squad-cto all"
BANNED='(^|[-_.])(v[0-9]+|final|old|copy|backup|bak|tmp|draft|new)([-_.]|$)'
SLUG_RE='^[a-z0-9][a-z0-9-]*$'

# key|path under docs/squad (placeholders <slug> <env> <role> <stage> <file> <pack>)|owner|kind|check target
TABLE='state|features/<slug>/state.json|delivery-manager|state|state
decisions|features/<slug>/records/decisions.md|squad-cto|ledger|decisions
errors|features/<slug>/records/errors.md|any role (finder, fixer, verifier)|ledger|errors
prd|features/<slug>/1-discovery/product-requirement.md|squad-po|artifact|prd
research|features/<slug>/1-discovery/market-research.md|squad-researcher|artifact|research
options|features/<slug>/1-discovery/options.md|squad-sa|artifact|options
brief|features/<slug>/1-discovery/decision-brief.md|squad-po|artifact|brief
plan-approval|features/<slug>/2-gate1/plan-approval.md|delivery-manager (CEO words)|approval|-
gate1-brief|features/<slug>/2-gate1/gate-brief.md|delivery-manager|artifact|-
requirements|features/<slug>/3-spec/requirements.md|squad-ba|artifact|requirements
architecture|features/<slug>/4-design/architecture.md|squad-sa|artifact|architecture
contract|features/<slug>/4-design/api-contract.yaml|squad-sa|artifact|contract
plan|features/<slug>/5-plan/implementation-plan.md|squad-lead|artifact|plan
test-plan|features/<slug>/6-verify/test-plan.md|squad-qa|artifact|testplan
test-cases|features/<slug>/6-verify/test-cases.md|squad-qa|artifact|testplan
report|features/<slug>/6-verify/regression-report-<env>.md|squad-qa|artifact|report
review|features/<slug>/6-verify/review-report.md|squad-reviewer|artifact|review
release-log|features/<slug>/7-release/release-log.md|squad-release|artifact|releaselog
cab-pack|features/<slug>/7-release/cab-pack.md|squad-release|artifact|cabpack
cab-approval|features/<slug>/8-gate2/cab-approval.md|delivery-manager (CEO words)|approval|-
gate2-brief|features/<slug>/8-gate2/gate-brief.md|delivery-manager|artifact|-
retro|features/<slug>/9-retro/retro.md|squad-cto|artifact|retro
evidence|features/<slug>/evidence/<stage>/<YYYYMMDD-HHMMSS>-<desc>.<ext>|any role|evidence|-
index|README.md|layout.sh index|generated|-
platform-baseline|knowledge/platform-baseline.md|delivery-manager (CEO words)|baseline|platform-baseline
business-baseline|knowledge/business-baseline.md|delivery-manager (CEO words)|baseline|business-baseline
lessons|knowledge/lessons.md|squad-cto|ledger|lessons
handbook|knowledge/roles/<role>.md|squad-cto (distill)|compiled|handbook
distill-log|knowledge/distill-log.md|squad-cto (distill)|ledger|distill-log
pack|knowledge/packs/<pack>/<file>|knowledge.sh export|generated|-
imported|knowledge/imported/<pack>/<file>|knowledge.sh import|generated|-
learned-rule|../../.claude/rules/squad-learned-<area>.md|squad-cto (distill)|compiled|learned'

row() { printf '%s\n' "$TABLE" | awk -F'|' -v k="$1" '$1 == k'; }
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64; }

cmd_path() {
  local key="$1" slug="${2:-}" x="${3:-}" r p
  r="$(row "$key")"; [[ -n "$r" ]] || { echo "unknown key '$key'" >&2; exit 64; }
  p="$(printf '%s' "$r" | cut -d'|' -f2)"
  case "$p" in *'<slug>'*) [[ "$slug" =~ $SLUG_RE ]] || { echo "path $key needs a kebab-case slug" >&2; exit 64; } ;; esac
  p="${p//<slug>/$slug}"; p="${p//<env>/$x}"; p="${p//<role>/$x}"; p="${p//<area>/$x}"
  if [[ "$key" == learned-rule ]]; then printf '%s\n' "${p#../../}"; else printf '%s/%s\n' "$SQ" "$p"; fi
}

# classify <path> → key owner kind target slug extra   |   UNKNOWN reason
classify() {
  local p="$1" rel inner slug f stage k
  case "$p" in /*) rel="${p#"$ROOT"/}"; [[ "$rel" == "$p" ]] && { printf 'UNKNOWN\toutside the project\n'; return; } ;; *) rel="${p#./}" ;; esac
  out() { local r; r="$(row "$1")"; printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$(printf '%s' "$r" | cut -d'|' -f3)" "$(printf '%s' "$r" | cut -d'|' -f4)" "$(printf '%s' "$r" | cut -d'|' -f5)" "${2:-}" "${3:-}"; }
  case "$rel" in
    .claude/rules/squad-learned-*.md)
      f="${rel#.claude/rules/squad-learned-}"; f="${f%.md}"
      [[ "$f" =~ $SLUG_RE ]] && { out learned-rule "" "$f"; return; }
      printf 'UNKNOWN\tlearned rule name must be squad-learned-<kebab-area>.md\n'; return ;;
    "$SQ"/README.md) out index; return ;;
    "$SQ"/.gitkeep) printf 'KEEP\t-\tplaceholder\t-\t\t\n'; return ;;
    "$SQ"/knowledge/lessons.md) out lessons; return ;;
    "$SQ"/knowledge/platform-baseline.md) out platform-baseline; return ;;
    "$SQ"/knowledge/business-baseline.md) out business-baseline; return ;;
    "$SQ"/knowledge/distill-log.md) out distill-log; return ;;
    "$SQ"/knowledge/roles/*.md)
      f="${rel#"$SQ"/knowledge/roles/}"; f="${f%.md}"
      [[ " $ROLES " == *" $f "* ]] && { out handbook "" "$f"; return; }
      printf 'UNKNOWN\thandbook must be knowledge/roles/<squad-role|all>.md\n'; return ;;
    "$SQ"/knowledge/packs/*|"$SQ"/knowledge/imported/*)
      inner="${rel#"$SQ"/knowledge/}"; f="${inner#*/}"; slug="${f%%/*}"; f="${f#*/}"
      [[ "$slug" =~ $SLUG_RE && "$f" =~ ^(manifest\.json|lessons\.md|roles/[a-z-]+\.md|rules/squad-learned-[a-z0-9-]+\.md)$ ]] \
        && { k="${inner%%/*}"; [[ "$k" == packs ]] && k=pack; out "$k" "" "$slug"; return; }
      printf 'UNKNOWN\tpacks hold only manifest.json, lessons.md, roles/<role>.md, rules/squad-learned-<area>.md\n'; return ;;
    "$SQ"/features/*)
      inner="${rel#"$SQ"/features/}"; slug="${inner%%/*}"; inner="${inner#*/}"
      [[ "$slug" =~ $SLUG_RE ]] || { printf 'UNKNOWN\tfeature folder must be kebab-case ASCII: %s\n' "$slug"; return; }
      case "$inner" in
        state.json) out state "$slug"; return ;;
        records/decisions.md) out decisions "$slug"; return ;;
        records/errors.md) out errors "$slug"; return ;;
        1-discovery/product-requirement.md) out prd "$slug"; return ;;
        1-discovery/market-research.md) out research "$slug"; return ;;
        1-discovery/options.md) out options "$slug"; return ;;
        1-discovery/decision-brief.md) out brief "$slug"; return ;;
        2-gate1/plan-approval.md) out plan-approval "$slug"; return ;;
        2-gate1/gate-brief.md) out gate1-brief "$slug"; return ;;
        3-spec/requirements.md) out requirements "$slug"; return ;;
        4-design/architecture.md) out architecture "$slug"; return ;;
        4-design/api-contract.yaml) out contract "$slug"; return ;;
        5-plan/implementation-plan.md) out plan "$slug"; return ;;
        6-verify/test-plan.md) out test-plan "$slug"; return ;;
        6-verify/test-cases.md) out test-cases "$slug"; return ;;
        6-verify/regression-report-dev.md|6-verify/regression-report-uat.md|6-verify/regression-report-pre.md)
          f="${inner#6-verify/regression-report-}"; out report "$slug" "${f%.md}"; return ;;
        6-verify/review-report.md) out review "$slug"; return ;;
        7-release/release-log.md) out release-log "$slug"; return ;;
        7-release/cab-pack.md) out cab-pack "$slug"; return ;;
        8-gate2/cab-approval.md) out cab-approval "$slug"; return ;;
        8-gate2/gate-brief.md) out gate2-brief "$slug"; return ;;
        9-retro/retro.md) out retro "$slug"; return ;;
        evidence/*/*)
          stage="${inner#evidence/}"; f="${stage#*/}"; stage="${stage%%/*}"
          [[ " $STAGES " == *" $stage "* ]] || { printf 'UNKNOWN\tevidence stage "%s" is not a stage name (%s)\n' "$stage" "$STAGES"; return; }
          [[ "$f" == */* ]] && { printf 'UNKNOWN\tno sub-folders inside evidence/%s/\n' "$stage"; return; }
          if [[ "$stage" == legacy ]]; then
            [[ "$f" =~ ^[A-Za-z0-9._-]+$ ]] && { out evidence "$slug" "$stage"; return; }
          elif [[ "$f" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+\.[a-z0-9.]+$ ]]; then
            [[ "$f" =~ $BANNED ]] || { out evidence "$slug" "$stage"; return; }
            printf 'UNKNOWN\tno version words (v2, final, old, copy, backup, tmp, draft, new) in file names — git keeps versions\n'; return
          fi
          printf 'UNKNOWN\tevidence file must be evidence/<stage>/<YYYYMMDD-HHMMSS>-<kebab-desc>.<ext>\n'; return ;;
      esac
      printf 'UNKNOWN\tnot in the layout: features/<slug>/%s (see scripts/squad/layout.sh table)\n' "$inner"; return ;;
    "$SQ"/*) printf 'UNKNOWN\tnot in the layout (docs/squad holds only README.md, features/, knowledge/)\n'; return ;;
  esac
  printf 'OUTSIDE\tnot a squad document\n'
}

legacy() {
  local found=0 d
  [[ -f "$ROOT/$SQ/LESSONS.md" ]] && { echo "$SQ/LESSONS.md"; found=1; }
  for d in "$ROOT/$SQ"/*/; do
    [[ -d "$d" ]] || continue
    case "$(basename "$d")" in features|knowledge) continue ;; esac
    echo "${d#"$ROOT"/}"; found=1
  done
  [[ $found -eq 1 ]]
}

case "${1:-}" in
  path) shift; [[ $# -ge 1 ]] || usage; cmd_path "$@" ;;
  feature-dir) [[ "${2:-}" =~ $SLUG_RE ]] || usage; echo "$SQ/features/$2" ;;
  classify) [[ $# -ge 2 ]] || usage; classify "$2" ;;
  legacy)
    if out="$(legacy)"; then echo "LEGACY_LAYOUT: documents in the pre-2.2 flat layout — run scripts/squad/migrate-layout.sh:"; printf '%s\n' "$out" | sed 's/^/  /'; exit 1; fi
    echo "layout is current"; exit 0 ;;
  check)
    bad=0; warn=0
    if lg="$(legacy)"; then echo "FAIL: legacy layout present (run scripts/squad/migrate-layout.sh): $(printf '%s' "$lg" | tr '\n' ' ')"; bad=$((bad + 1)); fi
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      c="$(classify "$f")"
      case "$c" in
        UNKNOWN*) echo "FAIL: $f — $(printf '%s' "$c" | cut -f2)"; bad=$((bad + 1)) ;;
      esac
      sz="$(wc -c < "$ROOT/$f" 2>/dev/null | tr -d ' ')"
      [[ "${sz:-0}" -gt 52428800 ]] && { echo "WARN: $f is $((sz / 1048576)) MB — GitHub refuses files over 100 MB (consider git LFS)"; warn=$((warn + 1)); }
    done < <(cd "$ROOT" && { find "$SQ" -type f 2>/dev/null; find .claude/rules -maxdepth 1 -name 'squad-learned-*' -type f 2>/dev/null; } | grep -v '/\.DS_Store$' | LC_ALL=C sort)
    if [[ $bad -gt 0 ]]; then echo "FAIL ($bad files outside the layout, $warn warnings)"; exit 1; fi
    echo "PASS (layout clean, $warn warnings)"; exit 0 ;;
  index)
    out="$ROOT/$SQ/README.md"; mkdir -p "$ROOT/$SQ"
    {
      echo "# Squad documents"
      echo
      echo "> GENERATED by \`scripts/squad/layout.sh index\` — do not edit by hand. Layout: \`scripts/squad/layout.sh table\`."
      echo
      echo "## Features"
      echo
      echo "| Feature | Stage | Tier | Gate 1 | Gate 2 | Open defects (S1/S2 · all) |"
      echo "|---|---|---|---|---|---|"
      n=0
      for st in "$ROOT/$SQ"/features/*/state.json; do
        [[ -f "$st" ]] || continue; n=$((n + 1)); s="$(basename "$(dirname "$st")")"
        if command -v jq >/dev/null 2>&1; then
          IFS=$'\t' read -r stage tier g1 g2 < <(jq -r '[.stage // "?", .tier // "?", .gates.ceo_plan // "?", .gates.ceo_golive // "?"] | @tsv' "$st" 2>/dev/null || echo "? ? ? ?")
        else stage="$(grep -o '"stage"[^,]*' "$st" | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/')"; tier="?"; g1="?"; g2="?"; fi
        ol="$(SQUAD_ROOT="$ROOT" "$(dirname "$0")/errors.sh" list --open --feature "$s" 2>/dev/null | tail -n +2)"
        sev="$(printf '%s\n' "$ol" | awk -F'\t' '$2 == "S1" || $2 == "S2"' | grep -c . || true)"; all="$(printf '%s\n' "$ol" | grep -c . || true)"
        echo "| [$s](features/$s/) | $stage | $tier | $g1 | $g2 | $sev · $all |"
      done
      [[ $n -eq 0 ]] && echo "| — | | | | | |"
      echo
      echo "## Knowledge"
      echo
      if [[ -x "$(dirname "$0")/knowledge.sh" ]]; then (cd "$ROOT" && "$(dirname "$0")/knowledge.sh" status 2>/dev/null) | sed 's/^/    /'; fi
      echo
      echo "Folders: \`features/<slug>/\` (state.json, records/, 1-discovery … 9-retro, evidence/), \`knowledge/\` (lessons.md, roles/, distill-log.md, packs/, imported/)."
    } > "$out.tmp" && mv "$out.tmp" "$out"
    echo "wrote ${out#"$ROOT"/}" ;;
  table)
    echo "| Key | Path (under docs/squad/) | Owner | Kind | check.sh target |"
    echo "|---|---|---|---|---|"
    printf '%s\n' "$TABLE" | awk -F'|' '{p = $2; if ($1 == "learned-rule") p = ".claude/rules/squad-learned-<area>.md (repo root)"; printf "| `%s` | `%s` | %s | %s | %s |\n", $1, p, $3, $4, ($5 == "-" ? "—" : "`" $5 "`")}' ;;
  *) usage ;;
esac
