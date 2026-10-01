# mcp-data-platform — release log

> **Architecture context.** This platform has **no shared hosted service**. Each of the nine MCP
> servers runs **per-user, local, over stdio** inside Claude Desktop/Code; the only shared backing
> store is the user's local Postgres+pgvector. Per `8-gate2/cab-approval.md`, "go-live" = **the local
> deployment the CEO runs on their own machine**, so `prod` in the entry below is the CEO's local
> macOS host. The operation is fully reversible (`docker compose … down -v` + removing the stdio
> server entry). `DEPLOY_MODE=script`; CAB approved at Gate 2 (`8-gate2/cab-approval.md` → `status:
> approved`).

> **DEMO DATA DISCLOSURE (read this).** The demo below runs on **sample/fake data, not real
> production sources**:
> - The nine real sources (Confluence, GitLab, OpenSearch, Kibana, CloudWatch, …) need **VPN +
>   credentials that are not present on this machine**, so a local HTTP **stub** served **two SAMPLE
>   Confluence pages** for the ingest demo.
> - **Hugging Face egress is blocked** (ADR-0010 / HF 403), so embeddings came from the repo's
>   `DeterministicFakeProvider` (hashed bag-of-words) exposed behind a local OpenAI-compatible HTTP
>   endpoint — the exact seam the e2e suite uses.
> - Therefore the demo proves the **ingest → pgvector → MCP-server-over-stdio path end to end against
>   the REAL Postgres+pgvector in Docker**, and the **read-only tool surface**, with **real JSON-RPC
>   envelopes and real rows from the DB**. It does **not** measure semantic relevance — that is CAB
>   caveat 1 / **NFR-003 UNVERIFIED**, to be measured in CHG-001 when egress/model are resolved. No
>   numbers here are fabricated; every figure is copied from the cited evidence file.

## 2026-10-01T18:36:49+07:00 · prod · deploy (local go-live + smoke + demo) · 0363fcc (branch claude/zealous-johnson-yb3t2q)

- **Environment / versions:** CEO local macOS, Docker 29.7.2, uv 0.11.7. Infra via
  `infra/docker-compose.yml` + local port override
  (`evidence/deploy-prod/compose.override.local-go-live.yml`, postgres published on host **5433**
  because a host-native PostgreSQL 17 already owns 5432). Images: `pgvector/pgvector:pg16`
  (pgvector **0.8.6**), `redis:7`, `apache/kafka:3.7.0` (KRaft, single node), `localstack/localstack:3`
  (sqs+sns). All four containers **healthy**.
- **Commands:**
  - `docker compose -f infra/docker-compose.yml -f <override> up -d` → exit 0; postgres/redis/kafka/localstack **healthy** (evidence: `20261001-183201-infra-health.txt`).
  - `MCP_INGEST_ADMIN_DSN=postgresql://mcp_admin:…@localhost:5433/mcp_kb uv run mcp-ingest db upgrade` → exit 0; migrations **0001–0006** applied (schema `kb`, current version `0006_review_followup`); `--dry-run` confirmed nothing pending (evidence: `20261001-183223-schema.txt`).
  - `uv run python evidence/deploy-prod/demo_seed_and_query.py` → ingest + stdio tool calls, exit 0 (evidence: `20261001-183344-demo-transcript.txt`).
  - `bash scripts/squad/smoke.sh prod` → exit 0 (evidence: `20261001-183444-smoke-prod.txt`).
  - Observation window (reduced for local): `20261001-183500-observation.txt`.
- **Endpoints:** no network listeners (per-user **stdio**). Backing services on
  `localhost:5433` (Postgres+pgvector), `localhost:6379` (Redis), `localhost:9092` (Kafka),
  `localhost:4566` (LocalStack). MCP servers launched as `python -m mcp_pgvector` (etc.) over stdio.
- **Schema created:** schema `kb` with tables `documents`, `chunks`, `ingest_failures`,
  `ingest_runs`, `ingest_source_state`, `schema_migrations` — the **as-built** model from migrations
  0001–0006 (ADR-0011). *Note:* the design doc's earlier entity names
  (`document_versions`/`entities`/`relationships`/`knowledge_summaries`/`sync_cursors`) were
  consolidated during implementation into this reviewed/QA'd schema; this is the schema that passed
  review (`6-verify/review-report.md` APPROVE) and the pgvector E2E (10/10). pgvector extension
  **0.8.6** installed; roles `mcp_ingest_rw` (DML) and `mcp_query_ro` (SELECT-only,
  `default_transaction_read_only=on`) present. (evidence: `20261001-183223-schema.txt`.)
- **Demo data ingested:** **2** sample Confluence documents → **2** chunks, embedding model
  `fake/hashed-bow` (1024-d). `mcp-ingest run` status **success**, exit 0. `mcp-ingest status`:
  confluence `document_count=2 chunk_count=2 staleness_hours=0.0 last_run_status=success`.
  (Stale rows from a prior QA e2e run were truncated first so the counts are unambiguous.)
- **Demo transcript (real JSON-RPC tool calls over stdio, read from the live DB):**
  - `mcp-pgvector` `tools/list` → `["kb_get_document","kb_list_sources","kb_semantic_search"]`
    (3 read-only tools; no write tool present).
  - `tools/call kb_list_sources {}` → `status: ok`, 1 source (confluence, 2 docs, model
    `fake/hashed-bow`), with a citation + `data_freshness`.
  - `tools/call kb_semantic_search {query:"how many times does the payment worker retry failed
    transactions", top_k:3}` → `status: ok`, returned the real stored chunk
    *"The payment worker retries failed transactions three times with exponential backoff…"* from
    page **1001** with `source_uri=https://wiki.example.test/wiki/spaces/PAY/pages/1001/payment-retry`,
    `similarity=0.5787`, `heading_path="Payment retry policy > Backoff"`, wrapped in the
    `<untrusted-content>` guard. **This is a real row returned by the real server from pgvector**
    (relevance is not asserted — fake embeddings; see disclosure).
  - **Negative (read-only proof):** `tools/call kb_delete_document {}` → `isError:true`,
    `"Unknown tool: kb_delete_document"`, `structuredContent:null` — the write tool does not exist on
    the surface. (evidence: `20261001-183344-demo-transcript.txt`.)
- **Smoke: pass** (~90 s total). `scripts/squad/smoke.sh prod` ran `doctor` on the three servers
  whose backend is live locally:
  - `mcp-pgvector doctor` → `credentials + read-only check: ok` (role `mcp_query_ro`, pgvector 0.8.6).
  - `mcp-redis doctor` → `ok` (ACL user `mcp_ro`).
  - `mcp-kafka doctor` → `ok` (bootstrap `localhost:9092`, auto-create off; SASL ACL check skipped — no principal).
  The 5 remote-only sources (Confluence/GitLab/OpenSearch/Kibana/CloudWatch) are **not** in this smoke
  (need VPN+creds); they are a manual Gate-C checklist (`evidence/legacy/SESSION-HANDOFF.md`). The
  `mcp-sqs-sns doctor` is **not green on LocalStack** because its startup gate calls
  `sts:GetCallerIdentity` and this LocalStack image has `sts` disabled — a LocalStack config
  limitation, not a product defect; the sqs/sns read path is proven in e2e TC-052 over moto.
- **SLI read-out:** no error-rate/p95 baseline exists for a per-user stdio server with no network
  listener; the SLI for this architecture is **liveness** (process + startup read-only gate). Over the
  observation window (two samples ~90 s apart): all four containers `healthy`, `mcp-pgvector doctor`
  exit 0 at both samples, **0 errors**. (evidence: `20261001-183500-observation.txt`.)
- **Rollback:** **n/a (not executed; not a failure)**. Reversal for this local go-live:
  `docker compose -f infra/docker-compose.yml -f docs/squad/features/mcp-data-platform/evidence/deploy-prod/compose.override.local-go-live.yml down -v`
  (stops + drops the Postgres volume), and remove the per-user stdio server entry from the Claude
  Desktop config / stop the ingest scheduler. Measured cost of this step is **< 60 s** (per
  `cab-pack.md` §6); no shared service to drain.
- **Notes / evidence:** `evidence/deploy-prod/20261001-183201-infra-health.txt`,
  `…-183223-schema.txt`, `…-183344-demo-transcript.txt`, `…-183444-smoke-prod.txt`,
  `…-183500-observation.txt`, plus the demo driver `demo_seed_and_query.py` and the compose override
  `compose.override.local-go-live.yml`. Not merged to main; not pushed; `state.json` unchanged
  (DM owns it).

### How the CEO can re-run the demo

```bash
cd /Users/manh.le/Desktop/MCP

# 1) Bring the infra up (postgres on host 5433 to dodge the native PG on 5432):
OV=docs/squad/features/mcp-data-platform/evidence/deploy-prod/compose.override.local-go-live.yml
docker compose -f infra/docker-compose.yml -f "$OV" up -d
docker compose -f infra/docker-compose.yml -f "$OV" ps        # wait until all 'healthy'

# 2) (first time only) set the dev role passwords used by the demo:
docker exec mcp-dev-postgres psql -U mcp_admin -d mcp_kb \
  -c "ALTER ROLE mcp_query_ro  LOGIN PASSWORD 'mcp_query_ro_dev_password'" \
  -c "ALTER ROLE mcp_ingest_rw LOGIN PASSWORD 'mcp_ingest_rw_dev_password'"

# 3) Apply migrations (idempotent):
MCP_INGEST_ADMIN_DSN=postgresql://mcp_admin:mcp_admin_dev_password@localhost:5433/mcp_kb \
  uv run mcp-ingest db upgrade

# 4) Seed sample data + run the real read-only stdio tool calls:
uv run python "$OV/../demo_seed_and_query.py"      # prints the JSON-RPC transcript

# 5) Smoke the live servers:
bash scripts/squad/smoke.sh prod

# Tear down (fully reversible, drops the demo DB volume):
docker compose -f infra/docker-compose.yml -f "$OV" down -v
```

To point the MCP servers at **real** sources later, replace the stub/fake env with real
`MCP_CONFLUENCE_*` / `MCP_GITLAB_*` / embedding (`MCP_INGEST_EMBEDDING_PROVIDER=local` or a real
HTTP endpoint) credentials over VPN — the code path is identical; only the env changes.

---

## 2026-10-01T21:18:14+07:00 · prod · deploy (live re-confirmation) · 0363fcc (branch claude/zealous-johnson-yb3t2q)

> Re-verification of the local go-live documented in the entry above, run live this session so the
> CEO can demo now. The infra from the initial cut-over was **still running** (postgres up ~3 h,
> redis/kafka/localstack up ~4 h); nothing was torn down. No `deploy.sh prod` was invoked — the
> containers were already up — so this entry records a **re-smoke + re-demo + observation** against
> the live system. **Same demo-data disclosure as above applies**: sample Confluence pages + fake
> (hashed-bow) embeddings; HF egress blocked; real Postgres+pgvector, real stdio JSON-RPC. No
> figures fabricated — every value is copied from the cited evidence files.

- **Environment / versions:** CEO local macOS, Docker **29.7.2**, uv **0.11.7**. Images unchanged:
  `pgvector/pgvector:pg16` (pgvector **0.8.6**), `redis:7`, `apache/kafka:3.7.0` (KRaft single node),
  `localstack/localstack:3` (sqs+sns running; sts/others disabled). Postgres published on host
  **5433** via `compose.override.local-go-live.yml`. All four containers **healthy** this session.
- **Commands (this session, exact):**
  - `docker compose -f infra/docker-compose.yml ps` → 4/4 **healthy** (postgres 5433, redis 6379,
    kafka 9092, localstack 4566).
  - `psql` schema/version/count checks → schema `kb`; tables `chunks, documents, ingest_failures,
    ingest_runs, ingest_source_state, schema_migrations`; migrations **0001–0006** applied; pgvector
    **0.8.6**; roles `mcp_admin, mcp_query_ro, mcp_ingest_rw`; `documents=2`, `chunks=2`.
  - `uv run python .../demo_seed_and_query.py` → exit **0** (evidence:
    `20261001-141650-demo-reconfirm.txt`).
  - `bash scripts/squad/smoke.sh prod` → exit **0**, `SMOKE PASS (prod)` (evidence:
    `20261001-141700-smoke-prod-reconfirm.txt`).
  - Observation window (reduced, 2 samples ~60 s apart) → evidence:
    `20261001-141713-observation-reconfirm.txt`.
- **Endpoints:** no network listeners (per-user **stdio**). Backing services on `localhost:5433`
  (Postgres+pgvector), `localhost:6379` (Redis), `localhost:9092` (Kafka), `localhost:4566`
  (LocalStack).
- **Demo data ingested:** idempotent re-run of the confluence source — `documents_seen=2`,
  `documents_upserted=0`, `documents_skipped=2`, `chunks_written=0` (unchanged content → the upsert
  dedup correctly skipped), status **success**, exit 0. `mcp-ingest status`: confluence
  `document_count=2 chunk_count=2 staleness_hours=0.0 last_run_status=success`. The DB still holds
  **2** sample documents / **2** chunks, model `fake/hashed-bow` (1024-d).
- **Demo transcript (real JSON-RPC over stdio, read from the live DB):**
  - `mcp-pgvector` `tools/list` → `["kb_get_document","kb_list_sources","kb_semantic_search"]`
    (3 read-only tools; no write tool).
  - `tools/call kb_list_sources {}` → `status: ok`, 1 source (confluence, 2 docs/2 chunks, model
    `fake/hashed-bow`), with citation + `data_freshness`.
  - `tools/call kb_semantic_search {query:"how many times does the payment worker retry failed
    transactions", top_k:3}` → `status: ok`, returned the real stored chunk *"The payment worker
    retries failed transactions three times with exponential backoff…"* from page **1001**,
    `source_uri=https://wiki.example.test/wiki/spaces/PAY/pages/1001/payment-retry`,
    `similarity=0.5787`, `heading_path="Payment retry policy > Backoff"`, wrapped in the
    `<untrusted-content>` guard. Real row, real server; relevance not asserted (fake embeddings).
  - **Negative (read-only proof):** `tools/call kb_delete_document {}` → `isError:true`,
    `"Unknown tool: kb_delete_document"` — the write tool does not exist on the surface.
    (evidence: `20261001-141650-demo-reconfirm.txt`.)
- **Smoke: pass.** `scripts/squad/smoke.sh prod` → `mcp-pgvector doctor` ok (role `mcp_query_ro`,
  pgvector 0.8.6), `mcp-redis doctor` ok (ACL user `mcp_ro`), `mcp-kafka doctor` ok
  (bootstrap `localhost:9092`, auto-create off; SASL ACL check skipped — no principal). The 5
  remote-only sources (Confluence/GitLab/OpenSearch/Kibana/CloudWatch) remain out of smoke (VPN+creds;
  manual Gate-C). `mcp-sqs-sns doctor` not green on this LocalStack image (`sts` disabled) — a
  LocalStack limitation, not a product defect; the sqs/sns read path is proven in e2e TC-052 over moto.
- **SLI read-out:** liveness is the SLI for a per-user stdio server (no network listener, so no
  error-rate/p95 baseline). Over the window (two samples ~60 s apart): all four containers `healthy`,
  `mcp-pgvector doctor` exit 0 at both samples, **0 errors**. (evidence:
  `20261001-141713-observation-reconfirm.txt`.)
- **Rollback:** **n/a (not executed; not a failure).** Reversal for this local go-live:
  `docker compose -f infra/docker-compose.yml -f docs/squad/features/mcp-data-platform/evidence/deploy-prod/compose.override.local-go-live.yml down -v`
  (stops + drops the Postgres volume), then remove the per-user stdio server entry from the Claude
  Desktop/Code config and stop the ingest scheduler. Measured cost **< 60 s** (per `cab-pack.md` §6);
  no shared service to drain.
- **Notes / evidence:** `evidence/deploy-prod/20261001-141650-demo-reconfirm.txt`,
  `…-141700-smoke-prod-reconfirm.txt`, `…-141713-observation-reconfirm.txt`. Not merged to main;
  not pushed; `state.json` unchanged (DM owns it). The CEO re-run steps are unchanged — see
  "How the CEO can re-run the demo" above.
