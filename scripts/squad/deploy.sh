#!/usr/bin/env bash
# scripts/squad/deploy.sh <uat|pre|prod>
# Seeded once by opc-init and then OWNED BY THIS PROJECT (never overwritten by the kit).
# squad-release implements the TODO blocks. Credentials come from the environment, never from this file.
#
# PRODUCTION GUARD — do not remove:
#   * script mode:   Claude Code asks the human before running "scripts/squad/deploy.sh prod" (permission rule).
#   * pipeline mode: the production job must require manual approval by the CEO (protected environment).
#   * this script refuses prod unless SQUAD_CAB_APPROVAL points to an approved cab-approval.md.
set -euo pipefail
ENV="${1:-}"
case "$ENV" in uat|pre|prod) ;; *) echo "usage: $(basename "$0") <uat|pre|prod>" >&2; exit 64 ;; esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE="$(grep "^DEPLOY_MODE=" "$ROOT/.claude/squad/config.env" 2>/dev/null | cut -d= -f2)"; MODE="${MODE:-script}"
[[ -f "$ROOT/scripts/squad/pipeline.env" ]] && . "$ROOT/scripts/squad/pipeline.env"
not_configured() { echo "NOT CONFIGURED: $(basename "$0") for env=$ENV mode=$MODE — squad-release must implement it (see architecture.md → Environments & deployment)" >&2; exit 3; }

if [[ "$ENV" == "prod" ]]; then
  f="${SQUAD_CAB_APPROVAL:-}"
  [[ -n "$f" && -f "$f" ]] && grep -q "^status: approved" "$f" \
    || { echo "REFUSED: prod deploy needs SQUAD_CAB_APPROVAL=<docs/squad/features/<slug>/8-gate2/cab-approval.md> with status: approved" >&2; exit 65; }
fi

case "$MODE" in
  script)
    case "$ENV" in
      uat)  not_configured ;;  # TODO: e.g. docker compose -f deploy/uat.yml up -d --build
      pre)  not_configured ;;  # TODO: e.g. ./deploy/pre.sh "$(git rev-parse HEAD)"
      prod) not_configured ;;  # TODO: same method as pre with prod config
    esac ;;
  pipeline)
    # TODO: trigger the CI/CD job for $ENV on ${SQUAD_PIPELINE_REF:-main} and wait for it to finish.
    # Examples (check your CLI version's --help):
    #   gitlab: glab ci run --branch "$SQUAD_PIPELINE_REF" --variables "SQUAD_ENV:$ENV"
    #   github: gh workflow run "$SQUAD_GH_WORKFLOW" --ref "$SQUAD_PIPELINE_REF" -f env="$ENV" && gh run watch
    not_configured ;;
esac
