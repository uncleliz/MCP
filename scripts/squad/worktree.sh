#!/usr/bin/env bash
# scripts/squad/worktree.sh create|path|remove|list [<slug>] [--reuse]
# One git worktree per squad feature: .claude/worktrees/squad-<slug> on branch squad/<slug>.
# create refuses a slug already in use (exit 5 SLUG_TAKEN) unless --reuse; remove needs the branch merged, or the
# feature closed (then the branch is kept). create writes
# .claude/squad/worktree.env in the worktree: SQUAD_SLOT, SQUAD_PORT_OFFSET (slot × 100) and COMPOSE_PROJECT_NAME,
# so features running side by side never share ports, containers or volumes.
# Run from the main checkout. Managed by opc-init — re-installing overwrites this file.
set -euo pipefail
ACTION="${1:-}"; SLUG="${2:-}"; REUSE=0; [[ "${3:-}" == --reuse ]] && REUSE=1
usage() { echo "usage: $(basename "$0") create|path|remove <slug>  |  list" >&2; exit 64; }
[[ "$ACTION" == list || -n "$SLUG" ]] || usage
[[ -z "$SLUG" || "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid slug '$SLUG' (kebab-case)" >&2; exit 64; }

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "NO_GIT: not a git repository — run the feature in the main checkout" >&2; exit 3; }
ROOT="$(git rev-parse --show-toplevel)"
if [[ "$(cd "$ROOT" && git rev-parse --git-dir)" != "$(cd "$ROOT" && git rev-parse --git-common-dir)" ]]; then
  echo "run this from the main checkout, not from a worktree" >&2; exit 64
fi
cd "$ROOT"
# squad meta dir + worktree root: Claude is primary when installed (matches the installer's meta dir);
# a Kiro-only install uses .kiro.
SQ=".claude/squad"; WTROOT=".claude/worktrees"
[[ -d "$ROOT/.claude/squad" ]] || { SQ=".kiro/squad"; WTROOT=".kiro/worktrees"; }
base_branch() {
  local b; b="$(grep "^MAIN_BRANCH=" "$SQ/config.env" 2>/dev/null | cut -d= -f2 || true)"
  [[ -n "$b" ]] && { echo "$b"; return; }
  b="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"; b="${b#origin/}"
  [[ -n "$b" ]] && { echo "$b"; return; }
  for b in main master; do git show-ref --verify --quiet "refs/heads/$b" && { echo "$b"; return; }; done
  git rev-parse --abbrev-ref HEAD
}
WT="$WTROOT/squad-$SLUG"; BR="squad/$SLUG"; BASE="$(base_branch)"

case "$ACTION" in
  path) echo "$ROOT/$WT" ;;
  list) git worktree list | grep -E '/\.(claude|kiro)/worktrees/squad-' || true ;;
  create)
    if [[ -d "$WT" ]]; then
      [[ $REUSE -eq 1 ]] && { echo "$ROOT/$WT"; exit 0; }
      echo "SLUG_TAKEN: a worktree for '$SLUG' already exists — pick another slug (e.g. $SLUG-2) or pass --reuse to continue that feature" >&2; exit 5
    fi
    if [[ $REUSE -eq 0 ]] && { git show-ref --verify --quiet "refs/heads/$BR" || [[ -d "docs/squad/features/$SLUG" ]]; }; then
      echo "SLUG_TAKEN: branch $BR or docs/squad/features/$SLUG already exists — pick another slug (e.g. $SLUG-2)" >&2; exit 5
    fi
    missing=""
    if [[ "$SQ" == ".kiro/squad" ]]; then
      kitfiles=".kiro/squad/config.env .kiro/agents/squad-cto.json .kiro/skills/squad/SKILL.md scripts/squad/deploy.sh"
    else
      kitfiles=".claude/settings.json .claude/squad/config.env .claude/agents/squad-cto.md .claude/skills/squad/SKILL.md scripts/squad/deploy.sh"
    fi
    for f in $kitfiles; do
      git ls-files --error-unmatch "$f" >/dev/null 2>&1 || missing="$missing $f"
    done
    if [[ -n "$missing" ]]; then
      echo "UNCOMMITTED_KIT: commit the squad install on $BASE first (a worktree only sees committed files):$missing" >&2
      echo "  see INSTALL.md §9 for the git add command" >&2; exit 4
    fi
    if git show-ref --verify --quiet "refs/heads/$BR"; then git worktree add "$WT" "$BR" >&2
    else git worktree add -b "$BR" "$WT" "$BASE" >&2; fi
    # per-worktree runtime isolation: ports and container names
    slot=1
    while grep -qsx "SQUAD_SLOT=$slot" .claude/worktrees/squad-*/.claude/squad/worktree.env .kiro/worktrees/squad-*/.kiro/squad/worktree.env; do slot=$((slot + 1)); done
    mkdir -p "$WT/$SQ"
    printf 'SQUAD_SLUG=%s\nSQUAD_SLOT=%s\nSQUAD_PORT_OFFSET=%s\nCOMPOSE_PROJECT_NAME=%s\n' \
      "$SLUG" "$slot" $((slot * 100)) "$(basename "$ROOT" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9\n' '-')-$SLUG" > "$WT/$SQ/worktree.env"
    # gitignored files the squad needs (Claude Code's .worktreeinclude covers --worktree sessions too)
    f=.claude/squad/notify.local.env
    if [[ -f "$f" ]]; then mkdir -p "$WT/$(dirname "$f")"; cp -p "$f" "$WT/$f"; fi
    echo "$ROOT/$WT" ;;
  remove)
    [[ -d "$WT" ]] || { echo "no worktree for $SLUG"; exit 0; }
    [[ -z "$(git -C "$WT" status --porcelain)" ]] || { echo "REFUSED: $WT has uncommitted changes" >&2; exit 65; }
    KEEP_BRANCH=0
    if ! git merge-base --is-ancestor "$BR" "$BASE"; then
      # a feature the CEO rejected (stage "closed") is never merged: remove its worktree but keep the branch
      grep -qsE '"stage" *: *"closed"' "$WT/docs/squad/features/$SLUG/state.json" \
        || { echo "REFUSED: $BR is not merged into $BASE (and the feature is not closed)" >&2; exit 65; }
      KEEP_BRANCH=1
    fi
    rm -f "$WT/.claude/squad/worktree.env" "$WT/.claude/squad/notify.local.env"
    if [[ $KEEP_BRANCH -eq 1 ]]; then
      git worktree remove "$WT" && echo "removed $WT; kept unmerged branch $BR (closed feature — delete it by hand when no longer needed)"
    else
      git worktree remove "$WT" && git branch -d "$BR" >/dev/null && echo "removed $WT and $BR"
    fi
    "$ROOT/scripts/squad/coord.sh" release-all "$SLUG" >/dev/null 2>&1 || true ;;
  *) usage ;;
esac
