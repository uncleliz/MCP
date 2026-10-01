#!/usr/bin/env bash
# scripts/squad/release-merge.sh <slug>
# C1 after Gate 2: merge squad/<slug> into the main branch (--no-ff) and tag release/<slug>-<yyyymmdd>.
# Run from the main checkout with the main branch checked out and no uncommitted tracked changes.
# Refuses (BEHIND_MAIN) when the branch does not already contain the main branch: another feature was released
# after this one was tested, so it must run scripts/squad/sync-main.sh in its worktree and re-verify first.
# Holds the "main" lock (scripts/squad/coord.sh) so two features never merge or commit on main at once.
# Managed by opc-init — re-installing overwrites this file.
set -euo pipefail
SLUG="${1:-}"
[[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "usage: $(basename "$0") <slug>" >&2; exit 64; }
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
[[ "$(git rev-parse --git-dir)" == "$(git rev-parse --git-common-dir)" ]] || { echo "run from the main checkout (ExitWorktree first)" >&2; exit 64; }
BR="squad/$SLUG"
SQ=".claude/squad"; WTROOT=".claude/worktrees"
[[ -d "$ROOT/.claude/squad" ]] || { SQ=".kiro/squad"; WTROOT=".kiro/worktrees"; }
BASE="$(grep "^MAIN_BRANCH=" "$SQ/config.env" 2>/dev/null | cut -d= -f2 || true)"
if [[ -z "$BASE" ]]; then
  BASE="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"; BASE="${BASE#origin/}"
fi
if [[ -z "$BASE" ]]; then
  for b in main master; do git show-ref --verify --quiet "refs/heads/$b" && { BASE="$b"; break; }; done
fi
[[ -n "$BASE" ]] || { echo "cannot determine the main branch; set MAIN_BRANCH= in $SQ/config.env" >&2; exit 64; }

APPROVAL=""
CAB="docs/squad/features/$SLUG/8-gate2/cab-approval.md"
for d in "." "$WTROOT/squad-$SLUG"; do
  [[ -f "$d/$CAB" ]] && APPROVAL="$d/$CAB"
done
if [[ -n "$APPROVAL" ]]; then grep -q '^status: approved' "$APPROVAL" || APPROVAL=""; fi
[[ -n "$APPROVAL" ]] || git show "$BR:$CAB" 2>/dev/null | grep -q '^status: approved' \
  || { echo "REFUSED: no approved cab-approval.md for $SLUG (Gate 2)" >&2; exit 65; }

git show-ref --verify --quiet "refs/heads/$BR" || { echo "no branch $BR" >&2; exit 64; }
[[ "$(git rev-parse --abbrev-ref HEAD)" == "$BASE" ]] || { echo "REFUSED: main checkout is on $(git rev-parse --abbrev-ref HEAD), expected $BASE" >&2; exit 65; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "REFUSED: main checkout has uncommitted changes" >&2; exit 65; }
WT="$WTROOT/squad-$SLUG"
if [[ -d "$WT" && -n "$(git -C "$WT" status --porcelain --untracked-files=no)" ]]; then
  echo "REFUSED: $WT has uncommitted changes — commit them on $BR first" >&2; exit 65
fi

# Take the main lock BEFORE the BEHIND_MAIN check: two features released at once must not both pass the check
# against the same main and then merge one after the other without the second being re-verified.
COORD="$ROOT/scripts/squad/coord.sh"
if [[ -x "$COORD" ]]; then
  "$COORD" lock main "$SLUG" --wait 300 >/dev/null || exit 75
  trap '"$COORD" unlock main "$SLUG" >/dev/null 2>&1' EXIT
fi
if ! git merge-base --is-ancestor "$BASE" "$BR"; then
  echo "BEHIND_MAIN: $BR does not contain the latest $BASE ($(git log --oneline "$BR..$BASE" | wc -l | tr -d ' ') new commit(s) from other features)." >&2
  echo "  In the feature worktree run scripts/squad/sync-main.sh, then re-run the stages whose evidence predates the sync." >&2
  exit 66
fi
git merge --no-ff "$BR" -m "release: $SLUG (squad, Gate 2 approved)"
TAG="release/$SLUG-$(date +%Y%m%d)"; n=2
while git show-ref --verify --quiet "refs/tags/$TAG"; do TAG="release/$SLUG-$(date +%Y%m%d)-$n"; n=$((n + 1)); done
git tag -a "$TAG" -m "squad release $SLUG"
echo "merged $BR into $BASE at $(git rev-parse --short HEAD), tagged $TAG"
