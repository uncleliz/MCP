#!/usr/bin/env bash
# scripts/squad/knowledge.sh — the team's compressed knowledge (docs/squad/knowledge/, see the squad-knowledge skill).
#   knowledge.sh status                 tiers at a glance: active lessons, handbooks, learned rules, last distill, due?
#   knowledge.sh active [--role R]      active lessons (not superseded, retired or promoted)
#   knowledge.sh usage                  per active lesson: times applied (HANDOFF applied:) and features since it was written
#   knowledge.sh due                    exit 0 when a distill is due (DISTILL_EVERY finished features, or > 40 active lessons)
#   knowledge.sh pending                finished features not yet covered by a distill
#   knowledge.sh next-k                 next distill id (K-nnn)
#   knowledge.sh export <name>          write knowledge/packs/<name>-<YYYYMMDD>/ (manifest, active lessons, handbooks, learned rules)
#   knowledge.sh import <pack-dir>      copy a pack into knowledge/imported/<pack>/ — input for the next distill, never merged directly
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
ROOT="${SQUAD_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../.." && pwd))}"
K="$ROOT/docs/squad/knowledge"; F="$ROOT/docs/squad/features"
CFG="$ROOT/.claude/squad/config.env"; [[ -f "$CFG" ]] || CFG="$ROOT/.kiro/squad/config.env"
cfg() { local v; v="$(grep "^$1=" "$CFG" 2>/dev/null | head -1 | cut -d= -f2-)"; printf '%s' "${v:-$2}"; }
EVERY="$(cfg DISTILL_EVERY 3)"; MAXL="$(cfg HANDBOOK_MAX_LINES 40)"; MAXACTIVE=40

# active lessons → "L-id<TAB>role<TAB>date<TAB>rule"
active() {
  [[ -f "$K/lessons.md" ]] || return 0
  awk '
    /^## L-[0-9]+ · / { split($0, h, " · "); id = h[1]; sub(/^## /, "", id); order[++n] = id; R[id] = h[2]; D[id] = h[4]; cur = id; next }
    /^### L-[0-9]+ · (retired|promoted) · / { split($0, h, " · "); x = h[1]; sub(/^### /, "", x); gone[x] = 1; cur = ""; next }
    /^##/ { cur = "" }
    cur != "" && /^- Rule:/ { r = $0; sub(/^- Rule: */, "", r); RU[cur] = r }
    cur != "" && /^- Supersedes:/ { s = $0; sub(/^- Supersedes: */, "", s); m = split(s, a, /[ ,]+/); for (i = 1; i <= m; i++) if (a[i] ~ /^L-[0-9]+$/) gone[a[i]] = 1 }
    END { for (k = 1; k <= n; k++) { x = order[k]; if (!(x in gone)) printf "%s\t%s\t%s\t%s\n", x, R[x], D[x], RU[x] } }
  ' "$K/lessons.md"
}
finished() {  # finished features: "slug<TAB>last history date"
  local st
  for st in "$F"/*/state.json; do
    [[ -f "$st" ]] || continue
    grep -qE '"stage" *: *"(done|closed)"' "$st" || continue
    printf '%s\t%s\n' "$(basename "$(dirname "$st")")" "$(grep -oE '"at" *: *"[^"]+"' "$st" | tail -1 | sed 's/.*: *"\(.*\)"/\1/')"
  done
}
covered() { [[ -f "$K/distill-log.md" ]] && grep -E '^- Features:' "$K/distill-log.md" | sed 's/^- Features: *//' | tr ', ' '\n\n' | grep -E '^[a-z0-9-]+$' | sort -u; }
pending() { local c; c="$(covered)"; finished | cut -f1 | while read -r s; do printf '%s\n' "$c" | grep -qx "$s" || echo "$s"; done; }

cmd="${1:-status}"; shift || true
case "$cmd" in
  active)
    role=""; [[ "${1:-}" == --role ]] && role="${2:-}"
    active | awk -F'\t' -v r="$role" 'r == "" || $2 == r || $2 == "all"' ;;
  pending) pending ;;
  next-k)
    n="$(grep -oE '^## K-[0-9]+' "$K/distill-log.md" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)"
    printf 'K-%03d\n' $(( 10#${n:-0} + 1 )) ;;
  usage)
    printf 'LESSON\tROLE\tAPPLIED\tFEATURES_SINCE\n'
    fin="$(finished)"
    active | while IFS=$'\t' read -r id role date _; do
      ap=0
      for st in "$F"/*/state.json; do
        [[ -f "$st" ]] || continue
        if command -v jq >/dev/null 2>&1; then
          c="$(jq --arg id "$id" '[.history[]?.applied[]? | select(. == $id)] | length' "$st" 2>/dev/null || echo 0)"
        else c="$(grep -o "\"$id\"" "$st" | wc -l | tr -d ' ')"; fi
        ap=$((ap + ${c:-0}))
      done
      since="$(printf '%s\n' "$fin" | awk -F'\t' -v d="$date" '$2 > d' | grep -c . || true)"
      printf '%s\t%s\t%s\t%s\n' "$id" "$role" "$ap" "$since"
    done ;;
  due)
    np="$(pending | grep -c . || true)"; na="$(active | grep -c . || true)"
    if [[ "$np" -ge "$EVERY" || "$na" -gt "$MAXACTIVE" ]]; then echo "DUE: $np finished feature(s) since the last distill (every $EVERY), $na active lessons (max $MAXACTIVE)"; exit 0; fi
    echo "not due: $np/$EVERY finished feature(s) since the last distill, $na/$MAXACTIVE active lessons"; exit 1 ;;
  status)
    na="$(active | grep -c . || true)"; all="$(grep -cE '^## L-[0-9]+ · ' "$K/lessons.md" 2>/dev/null || true)"; all="${all:-0}"
    last="$(grep -E '^## K-[0-9]+ · ' "$K/distill-log.md" 2>/dev/null | tail -1 | cut -d' ' -f2,4)"
    echo "Tier 1 lessons: $na active of $all written (max $MAXACTIVE active)"
    printf 'Tier 2 handbooks:'; hb=0
    for h in "$K"/roles/*.md; do [[ -f "$h" ]] || continue; hb=1; printf ' %s=%s' "$(basename "$h" .md)" "$(grep -c '^- ' "$h")"; done
    [[ $hb -eq 0 ]] && printf ' none yet'; printf ' (max %s rules each)\n' "$MAXL"
    echo "Tier 3 learned rules: $(find "$ROOT/.claude/rules" -maxdepth 1 -name 'squad-learned-*.md' 2>/dev/null | grep -c . || true)"
    echo "Last distill: ${last:-never}; finished features since: $(pending | grep -c . || true) (distill every $EVERY)"
    echo "Imported packs waiting for a distill: $(find "$K/imported" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | grep -c . || true)" ;;
  export)
    name="${1:-}"; [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "usage: knowledge.sh export <kebab-name>" >&2; exit 64; }
    d="$K/packs/$name-$(date +%Y%m%d)"; [[ -e "$d" ]] && { echo "REFUSED: $d exists" >&2; exit 65; }
    mkdir -p "$d/roles" "$d/rules"
    { echo "# Exported active lessons"; echo
      ids="$(active | cut -f1)"
      awk -v ids=" $(printf '%s ' $ids)" '/^## L-[0-9]+ · / { id = $2; keep = index(ids, " " id " ") > 0 } /^### / { keep = 0 } keep' "$K/lessons.md" 2>/dev/null
    } > "$d/lessons.md"
    cp "$K"/roles/*.md "$d/roles/" 2>/dev/null || true
    cp "$ROOT"/.claude/rules/squad-learned-*.md "$d/rules/" 2>/dev/null || true
    rmdir "$d/roles" "$d/rules" 2>/dev/null || true
    sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo none)"
    printf '{\n  "pack": "%s",\n  "source_project": "%s",\n  "source_commit": "%s",\n  "created": "%s",\n  "active_lessons": %s,\n  "handbooks": %s,\n  "learned_rules": %s\n}\n' \
      "$(basename "$d")" "$(basename "$ROOT")" "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(active | grep -c . || true)" \
      "$(find "$d/roles" -type f 2>/dev/null | grep -c . || true)" "$(find "$d/rules" -type f 2>/dev/null | grep -c . || true)" > "$d/manifest.json"
    echo "exported ${d#"$ROOT"/}" ;;
  import)
    src="${1:-}"; [[ -f "$src/manifest.json" ]] || { echo "usage: knowledge.sh import <pack-dir with manifest.json>" >&2; exit 64; }
    name="$(basename "$src")"; [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "pack folder name must be kebab-case" >&2; exit 64; }
    while IFS= read -r f; do
      rel="${f#"$src"/}"
      [[ "$rel" =~ ^(manifest\.json|lessons\.md|roles/[a-z-]+\.md|rules/squad-learned-[a-z0-9-]+\.md)$ ]] || { echo "REFUSED: unexpected file in pack: $rel" >&2; exit 65; }
    done < <(find "$src" -type f)
    d="$K/imported/$name"; [[ -e "$d" ]] && { echo "REFUSED: $d exists" >&2; exit 65; }
    mkdir -p "$d"; cp -R "$src"/. "$d"/
    echo "imported into ${d#"$ROOT"/} — the next distill reviews it (nothing is applied until then)" ;;
  *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64 ;;
esac
