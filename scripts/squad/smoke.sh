#!/usr/bin/env bash
# scripts/squad/smoke.sh <uat|pre|prod> — fast post-deploy health check; exit 0 = healthy.
# Seeded once by opc-init, then owned by this project. squad-release implements it.
set -euo pipefail
ENV="${1:-}"
case "$ENV" in uat|pre|prod) ;; *) echo "usage: $(basename "$0") <uat|pre|prod>" >&2; exit 64 ;; esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE="$(grep "^DEPLOY_MODE=" "$ROOT/.claude/squad/config.env" 2>/dev/null | cut -d= -f2)"; MODE="${MODE:-script}"
[[ -f "$ROOT/scripts/squad/pipeline.env" ]] && . "$ROOT/scripts/squad/pipeline.env"
not_configured() { echo "NOT CONFIGURED: $(basename "$0") for env=$ENV mode=$MODE — squad-release must implement it (see architecture.md → Environments & deployment)" >&2; exit 3; }

# mcp-data-platform local go-live smoke (squad-release, 2026-10-01).
# Architecture note (4-design/architecture.md "Environments & deployment"): this platform has NO
# shared hosted service. "deploy" = per-user local MCP servers over stdio; "prod" here = the CEO's
# local machine. Smoke therefore = `uv run mcp-<server> doctor` (liveness = process + startup
# read-only credential gate) for the servers whose backend is live locally in Docker Compose, plus
# the live pgvector data server. The 5 remote-only sources (Confluence/GitLab/OpenSearch/Kibana/
# CloudWatch) need VPN+creds and are covered by a manual Gate-C checklist, not this smoke.
#
# Config comes from the environment (never hard-coded creds). Defaults match infra/docker-compose.yml
# with the local go-live port override (postgres on 5433). Override via:
#   SMOKE_PG_RO_DSN, SMOKE_REDIS_URL, SMOKE_KAFKA_BOOTSTRAP, SMOKE_EMBED_URL, SMOKE_EMBED_MODEL
smoke_local() {
  local rc=0
  local pg_dsn="${SMOKE_PG_RO_DSN:-postgresql://mcp_query_ro:mcp_query_ro_dev_password@localhost:5433/mcp_kb}"
  local redis_url="${SMOKE_REDIS_URL:-redis://mcp_ro:mcp_ro_dev_password@localhost:6379/0}"
  local kafka="${SMOKE_KAFKA_BOOTSTRAP:-localhost:9092}"
  local embed_url="${SMOKE_EMBED_URL:-http://127.0.0.1:1/v1}"
  local embed_model="${SMOKE_EMBED_MODEL:-fake/hashed-bow}"

  echo "== smoke: mcp-pgvector doctor (live Postgres+pgvector, read-only role) =="
  MCP_PGVECTOR_DSN="$pg_dsn" \
    MCP_INGEST_EMBEDDING_PROVIDER=http MCP_INGEST_EMBEDDING_URL="$embed_url" \
    MCP_INGEST_EMBEDDING_MODEL="$embed_model" MCP_INGEST_EMBEDDING_DIMENSIONS=1024 \
    uv run mcp-pgvector doctor || rc=1

  echo "== smoke: mcp-redis doctor (live Redis, mcp_ro ACL user) =="
  MCP_REDIS_URL="$redis_url" uv run mcp-redis doctor || rc=1

  echo "== smoke: mcp-kafka doctor (live Kafka KRaft, auto-create off) =="
  MCP_KAFKA_BOOTSTRAP_SERVERS="$kafka" MCP_ALLOW_UNVERIFIED_CREDENTIALS=true \
    uv run mcp-kafka doctor || rc=1

  if [[ $rc -eq 0 ]]; then echo "SMOKE PASS ($ENV)"; else echo "SMOKE FAIL ($ENV)" >&2; fi
  return $rc
}

case "$ENV" in
  uat|pre|prod) smoke_local ;;
esac
