#!/usr/bin/env bash
# scripts/squad/errors.sh — the squad's error ledger across features
# (docs/squad/features/*/records/errors.md, worktrees included).
#   errors.sh list [--open] [--feature SLUG] [--category CAT] [--role ROLE] [--severity S1,S2]
#   errors.sh summary [--role ROLE]      patterns to avoid: by category, where introduced, which stages let them
#                                        escape, recurring ones, open S1/S2, and the preventions already in place
#   errors.sh open <feature-dir>         exit 1 if the feature has an open S1/S2 defect (gate before CAB)
#   errors.sh next <slug>                next free id, e.g. E-otp-login-004
# Format of errors.md: see the squad-errors skill. Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
ROOT="${SQUAD_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
MAIN="$(cd "$ROOT" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's#/\.git$##')"
[[ -n "${SQUAD_ROOT:-}" || ! -d "$MAIN" ]] && MAIN="$ROOT"
CATS="requirement design contract code test data security config deploy estimate process"
role_cats() {
  case "${1#squad-}" in
    po) echo "requirement estimate" ;;            researcher) echo "estimate design" ;;
    sa) echo "design contract security data" ;;   ba) echo "requirement" ;;
    lead) echo "estimate design process" ;;       qa) echo "test requirement code" ;;
    backend) echo "code contract security data config" ;; frontend) echo "code contract security" ;;
    reviewer) echo "code security contract data test" ;;  release) echo "deploy config data" ;;
    *) echo "$CATS" ;;
  esac
}
files() {
  { ls "$ROOT"/docs/squad/features/*/records/errors.md "$MAIN"/docs/squad/features/*/records/errors.md \
       "$MAIN"/.claude/worktrees/squad-*/docs/squad/features/*/records/errors.md \
       "$MAIN"/.kiro/worktrees/squad-*/docs/squad/features/*/records/errors.md 2>/dev/null || true; } | awk '!seen[$0]++'
}
# TSV: id sev date status feature category found introduced escaped recurrence symptom prevention
records() {
  local fs; fs="$(files)"; [[ -z "$fs" ]] && return 0
  # shellcheck disable=SC2086
  awk '
    function flush() { if (id != "") { if (!(id in seen)) { seen[id] = 1; order[++n] = id }; F[id] = feat; S[id] = sev; D[id] = dt; C[id] = cat; FO[id] = fnd; I[id] = intro; E[id] = esc; R[id] = rec; SY[id] = sym } id = "" }
    FNR == 1 { flush(); feat = FILENAME; sub(/\/records\/errors\.md$/, "", feat); sub(/.*\//, "", feat); mode = "" }
    /^## E-[a-z0-9-]+-[0-9]+ · S[1-4] · / { flush(); split($0, h, " · "); id = h[1]; sub(/^## /, "", id); sev = h[2]; dt = h[3]
      cat = ""; fnd = ""; intro = ""; esc = ""; rec = "none"; sym = ""; mode = "open"; next }
    /^### E-[a-z0-9-]+-[0-9]+ · (fix|verified|accepted) · / { flush(); split($0, h, " · "); rid = h[1]; sub(/^### /, "", rid); mode = h[2]
      if (mode == "verified") ST[rid] = "closed"; else if (mode == "accepted") ST[rid] = "accepted"; else if (ST[rid] == "") ST[rid] = "fixed"; next }
    mode == "open" && /^- Category:/ { cat = $0; sub(/^- Category: */, "", cat) }
    mode == "open" && /^- Found:/ { fnd = $0; sub(/^- Found: */, "", fnd); sub(/ ·.*/, "", fnd) }
    mode == "open" && /^- Introduced:/ { intro = $0; sub(/^- Introduced: */, "", intro) }
    mode == "open" && /^- Escaped:/ { esc = $0; sub(/^- Escaped: */, "", esc) }
    mode == "open" && /^- Recurrence of:/ { rec = $0; sub(/^- Recurrence of: */, "", rec) }
    mode == "open" && /^- Symptom:/ { sym = $0; sub(/^- Symptom: */, "", sym) }
    mode == "fix" && /^- Prevention:/ { p = $0; sub(/^- Prevention: */, "", p); P[rid] = p }
    END { flush(); for (k = 1; k <= n; k++) { x = order[k]; st = (ST[x] == "" ? "open" : ST[x])
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", x, S[x], D[x], st, F[x], (C[x] == "" ? "unknown" : C[x]), FO[x], (I[x] == "" ? "unknown" : I[x]), (E[x] == "" ? "none" : E[x]), R[x], SY[x], P[x] } }
  ' $fs
}

cmd="${1:-summary}"; shift || true
OPEN=0; FEAT=""; CAT=""; ROLE=""; SEV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --open) OPEN=1; shift ;; --feature) FEAT="$2"; shift 2 ;; --category) CAT="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;; --severity) SEV="$2"; shift 2 ;;
    *) ARG="$1"; shift ;;
  esac
done
filter() {
  local cats=""; [[ -n "$ROLE" ]] && cats=" $(role_cats "$ROLE") "
  awk -F'\t' -v o="$OPEN" -v f="$FEAT" -v c="$CAT" -v cats="$cats" -v s=",$SEV," '
    (o == 0 || $4 == "open" || $4 == "fixed") && (f == "" || $5 == f) && (c == "" || $6 == c) &&
    (cats == "" || index(cats, " " $6 " ") > 0) && (s == ",," || index(s, "," $2 ",") > 0)'
}

case "$cmd" in
  list)
    printf 'ID\tSEV\tSTATUS\tCATEGORY\tFOUND\tINTRODUCED\tSYMPTOM\n'
    records | filter | awk -F'\t' '{printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n", $1, $2, $4, $6, $7, $8, $11}' ;;
  open)
    dir="${ARG:-}"; [[ -f "$dir/records/errors.md" ]] || { echo "no records/errors.md in ${dir:-?} — no defects recorded"; exit 0; }
    FEAT="$(basename "$dir")"
    o="$(records | awk -F'\t' -v f="$FEAT" '$5 == f && ($4 == "open" || $4 == "fixed") && ($2 == "S1" || $2 == "S2") {print $1 " " $2 " " $4 ": " $11}')"
    if [[ -n "$o" ]]; then echo "open S1/S2 defects in $FEAT:"; echo "$o"; exit 1; fi
    echo "no open S1/S2 defects in $FEAT"; exit 0 ;;
  next)
    slug="${ARG:-}"; [[ -n "$slug" ]] || { echo "usage: errors.sh next <slug>" >&2; exit 64; }
    last="$(records | awk -F'\t' -v f="$slug" '$5 == f {sub(/.*-/, "", $1); if ($1 + 0 > m) m = $1 + 0} END {print m + 0}')"
    printf 'E-%s-%03d\n' "$slug" $((last + 1)) ;;
  summary)
    all="$(records | filter)"
    if [[ -z "$all" ]]; then echo "error ledger: no defects recorded${ROLE:+ in the categories of $ROLE}"; exit 0; fi
    echo "Error ledger${ROLE:+ — categories for $ROLE: $(role_cats "$ROLE")}"
    echo "  defects: $(printf '%s\n' "$all" | wc -l | tr -d ' ') in $(printf '%s\n' "$all" | cut -f5 | sort -u | wc -l | tr -d ' ') feature(s); open: $(printf '%s\n' "$all" | awk -F'\t' '$4 == "open" || $4 == "fixed"' | wc -l | tr -d ' ')"
    echo "  by category:   $(printf '%s\n' "$all" | cut -f6 | sort | uniq -c | sort -rn | awk '{printf "%s=%s ", $2, $1}')"
    echo "  introduced in: $(printf '%s\n' "$all" | cut -f8 | sort | uniq -c | sort -rn | head -5 | awk '{printf "%s=%s ", $2, $1}')"
    echo "  escaped past:  $(printf '%s\n' "$all" | cut -f9 | tr ',' '\n' | sed 's/^ *//; s/ *$//' | grep -v '^none$' | grep -v '^$' | sort | uniq -c | sort -rn | head -5 | awk '{printf "%s=%s ", $2, $1}')"
    rec="$(printf '%s\n' "$all" | awk -F'\t' '$10 != "none" && $10 != "" {print "    " $1 " repeats " $10 ": " $11}')"
    pat="$(printf '%s\n' "$all" | awk -F'\t' '{k = $6 " introduced in " $8; n[k]++; if (!(k SUBSEP $5 in seen)) {seen[k SUBSEP $5] = 1; fe[k]++}} END {for (k in n) if (n[k] >= 3 && fe[k] >= 2) print "    " k ": " n[k] " times in " fe[k] " features"}')"
    if [[ -n "$rec$pat" ]]; then echo "  RECURRING — prevent these first:"; [[ -n "$rec" ]] && echo "$rec"; [[ -n "$pat" ]] && echo "$pat"; fi
    op="$(printf '%s\n' "$all" | awk -F'\t' '($4 == "open" || $4 == "fixed") && ($2 == "S1" || $2 == "S2") {print "    " $1 " " $2 " (" $4 "): " $11}')"
    [[ -n "$op" ]] && { echo "  open S1/S2:"; echo "$op"; }
    echo "  preventions in place (latest 10):"
    printf '%s\n' "$all" | awk -F'\t' '$12 != "" {print "    " $1 " [" $6 "] " $12}' | tail -10 ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64 ;;
esac
