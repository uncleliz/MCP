#!/usr/bin/env bash
# scripts/squad/rollback.sh <uat|pre|prod> — return the environment to the previous release.
# Seeded once by opc-init, then owned by this project. squad-release implements it.
# Rolling back prod needs NO approval (squad policy: roll back first, report after).
set -euo pipefail
ENV="${1:-}"
case "$ENV" in uat|pre|prod) ;; *) echo "usage: $(basename "$0") <uat|pre|prod>" >&2; exit 64 ;; esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE="$(grep "^DEPLOY_MODE=" "$ROOT/.claude/squad/config.env" 2>/dev/null | cut -d= -f2)"; MODE="${MODE:-script}"
[[ -f "$ROOT/scripts/squad/pipeline.env" ]] && . "$ROOT/scripts/squad/pipeline.env"
not_configured() { echo "NOT CONFIGURED: $(basename "$0") for env=$ENV mode=$MODE — squad-release must implement it (see architecture.md → Environments & deployment)" >&2; exit 3; }

case "$MODE" in
  script)   not_configured ;;  # TODO: redeploy the previous tag, e.g. release/<slug>-<date> before the current one
  pipeline) not_configured ;;  # TODO: trigger the rollback job (its environment must NOT require manual approval)
esac
