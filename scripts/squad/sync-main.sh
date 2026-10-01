#!/usr/bin/env bash
# scripts/squad/sync-main.sh — run INSIDE a feature worktree (or on the feature branch).
# Brings the main branch into squad/<slug> so the feature is built and tested on top of everything already
# released by other features. Exit 0 = up to date or merged cleanly (prints whether anything changed),
# 2 = merge conflicts left for resolution (files listed), 64 = wrong place.
# After a sync that changed something, the stages whose evidence predates it are stale (see the /squad skill).
# Managed by opc-init — re-installing overwrites this file.
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not a git repository" >&2; exit 64; }
cd "$ROOT" || exit 64
BR="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BR" == squad/* ]] || { echo "run on a squad/<slug> branch (current: $BR)" >&2; exit 64; }
BASE="$(grep '^MAIN_BRANCH=' .kiro/squad/config.env 2>/dev/null | cut -d= -f2 || grep '^MAIN_BRANCH=' .claude/squad/config.env 2>/dev/null | cut -d= -f2 || true)"
if [[ -z "$BASE" ]]; then BASE="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"; BASE="${BASE#origin/}"; fi
if [[ -z "$BASE" ]]; then for b in main master; do git show-ref --verify --quiet "refs/heads/$b" && { BASE="$b"; break; }; done; fi
[[ -n "$BASE" ]] || { echo "cannot determine the main branch; set MAIN_BRANCH= in .kiro/squad/config.env or .claude/squad/config.env" >&2; exit 64; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "REFUSED: commit the feature's work before syncing" >&2; exit 65; }
if git merge-base --is-ancestor "$BASE" HEAD; then echo "UP_TO_DATE: $BR already contains $BASE"; exit 0; fi
new="$(git log --oneline HEAD.."$BASE" | wc -l | tr -d ' ')"
if git merge --no-ff --no-edit -m "sync: merge $BASE into $BR" "$BASE" >/dev/null 2>&1; then
  echo "SYNCED: merged $new commit(s) from $BASE into $BR — re-run the stages whose evidence predates this commit"
  git diff --stat HEAD^1 HEAD | tail -1
  exit 0
fi
echo "CONFLICT: merging $BASE into $BR needs resolution in:"
git diff --name-only --diff-filter=U | sed 's/^/  /'
echo "Resolve each file (owner role per path), then: git add <files> && git commit --no-edit; or abort: git merge --abort"
exit 2
