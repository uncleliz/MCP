#!/usr/bin/env bash
# scripts/squad/migrate-layout.sh [--dry-run] [--force]
# Moves squad documents from the pre-2.2 flat layout (docs/squad/<slug>/<file>, docs/squad/LESSONS.md) into the
# current layout (docs/squad/features/<slug>/<phase>/<file>, docs/squad/knowledge/lessons.md) with `git mv`, so
# file history is kept. Unknown files go to features/<slug>/evidence/legacy/. Safe to re-run.
# Refuses while a feature is still in progress (finish it, or --force if you will migrate its worktree too).
# Run from the main checkout. Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
DRY=0; FORCE=0
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; --force) FORCE=1 ;; *) echo "usage: $(basename "$0") [--dry-run] [--force]" >&2; exit 64 ;; esac; done
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../.." && pwd))"
cd "$ROOT" || exit 64
SQ=docs/squad
GIT=0; git rev-parse --is-inside-work-tree >/dev/null 2>&1 && GIT=1

legacy_dirs() { for d in "$SQ"/*/; do [[ -d "$d" ]] || continue; case "$(basename "$d")" in features|knowledge) continue ;; esac; echo "${d%/}"; done; }
dirs="$(legacy_dirs)"
if [[ -z "$dirs" && ! -f "$SQ/LESSONS.md" ]]; then echo "nothing to migrate — layout is current"; exit 0; fi

# in-progress features (legacy folders and feature worktrees still on the old layout)
busy=""
for st in $(for d in $dirs; do echo "$d/state.json"; done) .claude/worktrees/squad-*/"$SQ"/*/state.json .kiro/worktrees/squad-*/"$SQ"/*/state.json; do
  [[ -f "$st" ]] || continue
  case "$st" in */features/*) continue ;; esac
  grep -qE '"stage" *: *"(done|closed)"' "$st" || busy="$busy $(dirname "$st")"
done
if [[ -n "$busy" && $FORCE -eq 0 ]]; then
  echo "REFUSED: features still in progress on the old layout:$busy" >&2
  echo "  Finish them first, or re-run with --force and then migrate each feature worktree the same way." >&2
  exit 65
fi

dest_of() {  # dest_of <slug> <file-name-in-legacy-folder>
  case "$2" in
    state.json) echo "state.json" ;;
    decisions.md|errors.md) echo "records/$2" ;;
    product-requirement.md|market-research.md|options.md|decision-brief.md) echo "1-discovery/$2" ;;
    plan-approval.md) echo "2-gate1/$2" ;;
    requirements.md) echo "3-spec/$2" ;;
    architecture.md|api-contract.yaml) echo "4-design/$2" ;;
    implementation-plan.md) echo "5-plan/$2" ;;
    test-plan.md|test-cases.md|review-report.md|regression-report-dev.md|regression-report-uat.md|regression-report-pre.md) echo "6-verify/$2" ;;
    release-log.md|cab-pack.md) echo "7-release/$2" ;;
    cab-approval.md) echo "8-gate2/$2" ;;
    retro.md) echo "9-retro/$2" ;;
    *) echo "evidence/legacy/$(printf '%s' "$2" | tr '/ ' '--' | tr -cd 'A-Za-z0-9._-')" ;;
  esac
}
move() {  # move <src> <dst>
  if [[ -e "$2" ]]; then echo "CONFLICT: $2 already exists — left $1 in place" >&2; conflicts=$((conflicts + 1)); return; fi
  echo "  $1 → $2"; moved=$((moved + 1))
  [[ $DRY -eq 1 ]] && return
  mkdir -p "$(dirname "$2")"
  if [[ $GIT -eq 1 ]] && git ls-files --error-unmatch "$1" >/dev/null 2>&1; then git mv "$1" "$2"; else mv "$1" "$2"; fi
}

moved=0; conflicts=0
echo "Migrating squad documents to the current layout$([[ $DRY -eq 1 ]] && echo ' (dry run)'):"
for d in $dirs; do
  slug="$(basename "$d")"
  if ! [[ "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "SKIPPED: $d — folder name is not a kebab-case slug; rename it first" >&2; conflicts=$((conflicts + 1)); continue
  fi
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    rel="${f#"$d"/}"
    move "$f" "$SQ/features/$slug/$(dest_of "$slug" "$rel")"
  done < <(find "$d" -type f | LC_ALL=C sort)
  [[ $DRY -eq 0 ]] && find "$d" -depth -type d -empty -delete 2>/dev/null
done
[[ -f "$SQ/LESSONS.md" ]] && move "$SQ/LESSONS.md" "$SQ/knowledge/lessons.md"

if [[ $DRY -eq 0 && -x "$(dirname "$0")/layout.sh" ]]; then "$(dirname "$0")/layout.sh" index >/dev/null; fi
echo "moved $moved file(s), $conflicts conflict(s)$([[ $DRY -eq 0 ]] && echo '; docs/squad/README.md regenerated')"
[[ $DRY -eq 0 && $GIT -eq 1 ]] && echo "Review with 'git status', then commit: git add -A docs/squad && git commit -m 'chore(squad): migrate to the 2.2 document layout'"
[[ $conflicts -eq 0 ]]
