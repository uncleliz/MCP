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

---

## 2026-10-02T15:09:38+07:00 · prod · deploy (CHG-003 real ingestion + real egress — Confluence Cloud first) · b898040 (branch claude/zealous-johnson-yb3t2q)

> **Outcome: BLOCKED AT STEP 1 (doctor read-only gate) — NO ingest, NO real egress pull, NO DB write.**
> CEO Gate 2 was approved (`8-gate2/cab-approval.md` → `status: approved`, CHG-003) and this was the
> first authorised REAL EGRESS attempt (first real Confluence Cloud pull). The deploy sequence stops
> and reports if any step fails; **Step 1 (the read-only credential doctor) FAILED**, so per the
> runbook the go-live was halted before any egress pull or any write to pgvector. The design gate did
> exactly what it is built to do: `doctor` refused a write-capable Atlassian account (TC-116 /
> architecture.md read-only startup gate). This is a **credential problem to escalate to the CEO**, not
> a code/deploy defect — nothing broke, nothing was rolled back, the corpus is unchanged.

- **Environment / versions:** CEO local macOS, Docker 29.7.2, uv 0.11.7. `DEPLOY_MODE=script`,
  `ENVIRONMENTS=uat,pre,prod`, prod = this local machine. pgvector container `mcp-dev-postgres`
  **Up (healthy)**, Postgres published on host **5433** (same container/pattern as the prior go-lives).
- **CAB / authorisation:** `8-gate2/cab-approval.md` present, `status: approved` (CHG-mcp-data-platform-chg003-20261002,
  CEO, 2026-10-02T14:57). D-007 READY_FOR_CAB. The prod-deploy guard in `scripts/squad/deploy.sh`
  (greps `^status: approved` on `$SQUAD_CAB_APPROVAL`) is satisfied by this file.
- **Target DB (DK5 context):** the live `kb` schema on `mcp-dev-postgres` (host 5433, db `mcp_kb`).
  **No DK5 snapshot was taken** — and intentionally so: the DB write phase (Step 2 onward) was never
  reached because Step 1 failed. The corpus therefore stayed in its pre-ingest state; `kb.documents`
  still holds only the **2 demo rows** (`source_type=confluence`, `fake/hashed-bow` sample pages) from
  the Oct-1 base go-live — verified `select source_type, count(*) → confluence|2`, i.e. **0 real rows
  ingested**. A restorable snapshot will be recorded immediately before the first real pull once a
  read-only token is supplied.
- **Commands (exact, this session):**
  - `set -a; source credentials/.ingest-sources.env; set +a` → loaded real ingest config (git-ignored
    hidden file). Non-secret scope confirmed: `base_url=https://tnexwm.atlassian.net/wiki`,
    `flavor=cloud`, `MCP_EGRESS_ALLOWLIST=*.atlassian.net`, `MCP_INGEST_ALLOW_LIVE_EGRESS=true`,
    `MCP_INGEST_CONFLUENCE_TEAM_SPACES` **EMPTY** (len=0). Token present (len=192); never echoed.
  - **Step 1** `uv run mcp-confluence doctor` → **exit 1, read-only check FAILED.**
    - `config: ok (base_url=https://tnexwm.atlassian.net/wiki, flavor=cloud)`
    - `credentials + read-only check: FAILED — account is not read-only (use a viewer-only service
      account); permitted write operations: create:page, update:page, create:comment,
      create:attachment, create:folder, create:whiteboard, create:slide, create:embed,
      create:database, + 2 ZenUML add-on create ops`.
  - Steps 2–6 (DK5 snapshot, migration verify, space discovery/pull, `mcp-ingest run`, `status`,
    `kb_semantic_search` verify, smoke, 30-min watch) → **NOT RUN** (sequence halted at Step 1 by rule
    "stop and report if any step fails; do not improvise around a failure").
- **Doctor read-only verdict:** **FAIL — account is WRITE-CAPABLE.** Per Step 1, this is a hard STOP:
  do NOT ingest; the CEO must supply a **viewer-only / read-only** Atlassian Cloud token (no
  create/edit/transition). Do not set `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` for a real token.
- **Real egress / ingest:** **none.** `MCP_INGEST_ALLOW_LIVE_EGRESS=true` was set in the env but no
  `mcp-ingest run` was launched, so no outbound pull to `*.atlassian.net` occurred on the ingest path.
  (Doctor's own auth call to the tenant is a read-only health probe, not an ingest pull.)
- **Spaces discovered:** **n/a — not reached.** `MCP_INGEST_CONFLUENCE_TEAM_SPACES` is empty; read-only
  space discovery was to run only after a read-only token passed doctor. Because Step 1 failed, no
  space list was pulled and no scope was chosen. (Next read-only token → discover spaces → ask CEO to
  choose scope before any pull; a bounded `--limit` first pull is preferred.)
- **Smoke / watch:** **not run** (go-live halted pre-deploy). No observation window opened.
- **Token-never-leaks invariant (E-009):** **HELD.** The doctor evidence file was grepped for the raw
  token value → **absent** (`OK: token not present in evidence file`); the FAILED report names only
  write-operation identifiers, no secret. The scrub wiring (`register_secret` at the client
  constructor) kept the token out of output.
- **Egress-default-deny invariant:** not exercised this session (no pull); unchanged from the verified
  CHG-003 build (ADR-0023 §6e: unlisted host refused before dial, single `check_egress` choke point).
- **Rollback:** **n/a (not executed; nothing to roll back).** No egress pull, no DB write, no new
  content, no observation window. The system remained in its stubbed/no-real-egress state. Rollback
  readiness is intact for the real attempt: `scripts/squad/rollback.sh prod` plus the <60 s manual
  steps (unset `MCP_EGRESS_ALLOWLIST` → default-deny every outbound host; remove the Atlassian token /
  unset `MCP_CONFLUENCE_API_TOKEN*`; stop the ingest connector), per `cab-pack.md` §6. Live egress is
  off whenever `MCP_INGEST_ALLOW_LIVE_EGRESS` is unset.
- **Classification:** this is **not** an error-ledger defect (no failed smoke, no broken environment,
  no executed rollback). `doctor` refusing a write-capable account is the read-only gate working as
  designed; the fix is operational (a read-only token from the CEO), so no `errors.md` entry is opened.
  `errors.sh open` → **no open S1/S2** (unchanged).
- **Next step:** **CEO to supply a least-privilege READ-ONLY Atlassian Cloud token** (viewer-only
  service account; can read the target spaces, cannot create/edit/transition). Then re-run the deploy
  sequence from Step 1 (doctor must show `read-only check: ok`), take the DK5 snapshot, run read-only
  space discovery (TEAM_SPACES is empty) and **confirm the space scope with the CEO before the first
  real pull** (bounded `--limit` preferred), then ingest → status → `kb_semantic_search` verify →
  smoke → 30-min watch. A new real-egress attempt is still covered by the existing CHG-003
  `cab-approval.md`; the only missing precondition is a read-only credential.
- **Notes / evidence:** `evidence/deploy-prod/20261002-150938-chg003-confluence-doctor.txt` (doctor
  report, token redacted, exit 1). Not merged to main; not pushed; `state.json` / `plan-approval.md` /
  `cab-approval.md` unchanged (DM owns them).

---

## 2026-10-02T15:37:40+07:00 · prod · deploy (CHG-003 retry with escape-hatch — WARN-and-serve + read-only space discovery) · b898040 (branch claude/zealous-johnson-yb3t2q)

> **Outcome: ESCAPE-HATCH INVOKED (D-008) → doctor's write-capable account no longer blocks serving;
> the SERVE path WARN-and-serves; read-only tool surface still 0 write tools; read-only space
> discovery completed over the real tenant. NO ingest/pull yet — PAUSED awaiting CEO space-scope
> confirmation.** This is the authorised retry of the 2026-10-02T15:09:38 Step-1 block. CEO Gate 2
> is approved (`8-gate2/cab-approval.md` → `status: approved`, CHG-003) and CTO D-008
> (`records/decisions.md`) APPROVES invoking `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` for the CEO's
> write-capable Atlassian token because the served surface is read-only (0 write tools) and there is
> no code path that writes to Confluence via MCP. Per the deploy sequence, the run stops after
> read-only space discovery and hands the space list back for the CEO to choose scope before any pull.

- **Environment / versions:** CEO local macOS, Docker, uv 0.11.7. `DEPLOY_MODE=script`,
  `ENVIRONMENTS=uat,pre,prod`, prod = this local machine. pgvector container `mcp-dev-postgres`
  **Up (healthy)**, Postgres on host **5433** (same container/pattern as the prior go-lives).
- **CAB / authorisation:** `8-gate2/cab-approval.md` present, `status: approved`
  (CHG-mcp-data-platform-chg003-20261002, CEO, 2026-10-02T14:57). CTO escape-hatch approval = **D-008**
  (`records/decisions.md`, 2026-10-02T15:33) — scoped invocation under the already-approved CHG-003
  Gate 2; **no new Gate 2** (nothing about egress/vendor/scope changed; the token was always the CEO's
  at run time). No `deploy.sh prod` was invoked — as at the prior go-lives, the infra was already up
  and this entry records the operator-runbook steps (doctor → serve-gate proof → surface cross-check →
  read-only discovery), halting before any pull per the stop-after-discovery rule.
- **Credential handling:** config loaded with `set -a; source credentials/.ingest-sources.env; set +a`
  (git-ignored hidden file). Non-secret scope confirmed: `base_url=https://tnexwm.atlassian.net/wiki`,
  `flavor=cloud`, `email=manhld5@…` (domain redacted in chat), `MCP_EGRESS_ALLOWLIST=*.atlassian.net`,
  `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`,
  `MCP_INGEST_CONFLUENCE_TEAM_SPACES` **EMPTY** (len=0). Token present (len=192); **never echoed**;
  every evidence file was grepped for the raw token → absent (see below).
- **Commands (exact, this session):**
  - `set -a; source credentials/.ingest-sources.env; set +a` → loaded real ingest config.
  - **Step 1a — doctor (diagnostic verdict):** `uv run mcp-confluence doctor` → **exit 1**,
    `config: ok (base_url=https://tnexwm.atlassian.net/wiki, flavor=cloud)` and
    `credentials + read-only check: FAILED — account is not read-only … permitted write operations:
    create:page, update:page, create:comment, create:attachment, create:folder, create:whiteboard,
    create:slide, create:embed, create:database, + 2 ZenUML add-on create ops`.
    **Important:** the `doctor` CLI (`cli.py::_doctor`) is a pure diagnostic — it calls
    `verify_credentials()` and reports the real verdict; it **does not** consult the escape-hatch, so
    it still reports FAILED/exit 1 by design. The escape-hatch (`MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`)
    lives in the **serve** startup gate (`runtime.py::run_credential_check`, line 209), not in doctor.
    So the authorised proof of "WARN-and-serve despite a write-capable account" is Step 1b, not doctor.
  - **Step 1b — serve WARN-and-serve proof (the escape-hatch path, D-008):**
    `printf '' | timeout 20 uv run mcp-confluence serve` (empty stdin → stdio EOF → clean exit after
    startup) → the startup credential gate did **not** refuse; it emitted exactly **one WARNING**:
    `"mcp-confluence: startup credential check failed or was skipped, but «redacted:high-entropy» —
    serving anyway."` then proceeded. This is the documented WARN-and-serve (ADR-0003 A1 / TC-116).
    The scrub even redacted a high-entropy fragment in the WARN message, confirming the E-009
    token-scrub wiring holds on the live path. **exit 0.**
  - **Step 1c — read-only tool-surface cross-check (despite the write-capable account):**
    `uv run python scripts/verify_tool_surface.py` → **50/50 checks passed, exit 0**:
    `registered=62 contract=62 diff=[]`, **0 write tools × 11 servers**, unknown-write tool rejected
    at the JSON-RPC layer for every server, doctor/serve/tools-dump present on all. The write-capable
    credential adds **no** write capability to the surface.
  - **Step 2 — read-only Confluence space discovery (NO ingest, NO write):**
    `uv run python …/evidence/deploy-prod/chg003_space_discovery.py` → **exit 0**. Builds
    `ConfluenceClient(enforce_egress=True)` and calls the shipped read-only `confluence_list_spaces`
    (GET `/rest/api/space`) through the single egress choke point (only `*.atlassian.net` dialable).
    **First real outbound egress to `*.atlassian.net` on this feature succeeded, read-only.**
  - **Step 3 — STOP after discovery** (per the sequence: do not run an unbounded first pull; hand the
    space list back for the CEO to choose scope). `mcp-ingest run` was **NOT** launched.
- **Doctor read-only verdict:** FAIL (account WRITE-CAPABLE) — **expected and overridden by D-008**
  via the serve escape-hatch, not by weakening doctor. See Step 1a/1b.
- **Escape-hatch WARN-and-serve:** **OK** — one WARN `serving anyway`, server proceeded (Step 1b).
- **Read-only surface cross-check:** **OK — 0 write tools across 11 servers (verify_tool_surface
  50/50)**; the write-capable token does not expand the surface.
- **Spaces discovered (read-only):** **100 returned** on the first page (the account can see more than
  100; the first page is the sample for scope choice). **37 global/team spaces** + personal spaces.
  The ingest-relevant team spaces include:
  `AS` (Accounting Space), `CAB`, `CASAMOA` (CASA_MOA), `CHM` (Change Management), `CL` (Cash Loan),
  `CRL` (Lending 2 - Credit Line), `CS` (Cyber Security), `CreditLine` (Credit_Line),
  `DC` (Digital Collection), `DD` (Direct Channel), `DEMO` (demo), `DMS` (Data Management Squad),
  `DT` (Data Team), `EA` (Engagement & Acquisition), `FCO` (FCCOM IT Operations),
  `FM` (Fraud Management), `FT` (FCCOM Tech), `IH` (Innovation Hub),
  `LBM` (Loan Business Management), `LOAN` (Loan Business Mgt), `MCL`/`ML` (Merchant Lending),
  `PAYMENT`, `PL` (Purpose Loan), `PM` (Project Management), `PMO`, `PVH` (Phòng vận hành),
  `Partnershi` (Partnership), `Platform`, `Productreq` (Product_requirement), `QD` (QA Department),
  plus `C2` (Core 2), `Compliance`, `LEO`, `LM` (Leadership & Management), `Legal`, `OE`
  (Operational Excellence). Full list (incl. ~60 personal `~…` spaces) is in the evidence file.
- **Real egress / ingest:** read-only space discovery egress to `*.atlassian.net` succeeded; **no
  ingest pull** (no `mcp-ingest run`), so **0 real rows** entered the corpus. `kb.documents` still
  holds only the 2 demo rows from the Oct-1 base go-live.
- **DK5 Postgres snapshot:** **not taken yet** — intentional: the DB write phase (ingest) was not
  reached. The restorable-snapshot id will be recorded immediately before the first real pull, once
  the CEO confirms the space scope (cab-pack §5 step 2 / D-007 DK5).
- **Smoke: not run** (go-live paused at space discovery before any pull; no observation window opened).
  The prod smoke + 30-min watch are prepared and run **after** the CEO confirms scope and the first
  bounded pull completes.
- **SLI read-out:** n/a this session — no pull, no observation window opened yet. (For this per-user
  stdio architecture the SLI is liveness + egress-default-deny + token-never-leaks, read during the
  post-pull 30-min watch.)
- **Token-never-leaks invariant (E-009):** **HELD.** Every evidence file was grepped for the raw
  token value → **absent** (`OK: token not present in evidence file` on all three). The serve WARN
  message redacted a high-entropy fragment rather than printing the secret.
- **Egress / read-only invariants:** egress stayed default-deny with the single choke point armed
  (`enforce_egress=True`); only `*.atlassian.net` was dialed; the served surface stayed read-only
  (0 write tools). Unchanged from the verified CHG-003 build (ADR-0023 §6e).
- **Rollback: n/a** (not executed; nothing to roll back — no ingest, no DB write, no new content, no
  observation window). Rollback readiness intact: `scripts/squad/rollback.sh prod` + the <60 s manual
  steps (unset `MCP_EGRESS_ALLOWLIST` → default-deny; remove/unset the token; stop the connector),
  per `cab-pack.md` §6. Live egress is off whenever `MCP_INGEST_ALLOW_LIVE_EGRESS` is unset.
- **Classification:** **not** an error-ledger defect — no failed smoke, no broken environment, no
  executed rollback. The escape-hatch invocation is an authorised D-008 operator action; `doctor`
  reporting the write-capable verdict is the diagnostic working as designed. `errors.sh open` →
  **no open S1/S2** (unchanged).
- **Next step:** **CEO to choose which Confluence space(s) to ingest** from the list above.
  **Proposed bounded safe default (orchestrator to confirm):** a single team space with a hard cap —
  set `MCP_INGEST_CONFLUENCE_TEAM_SPACES=DT` (Data Team) and run
  `uv run mcp-ingest run --source confluence --limit 20 --dry-run` first, then
  `uv run mcp-ingest run --source confluence --limit 20`. `MCP_INGEST_CONFLUENCE_TEAM_SPACES` is
  currently EMPTY and the connector requires at least one space, so a scope choice is mandatory before
  any pull. After scope is confirmed: take the DK5 snapshot (log its id) → bounded pull →
  `mcp-ingest status --json` → `kb_semantic_search` verify → `smoke.sh prod` → 30-min watch. The pull
  remains covered by the existing CHG-003 `cab-approval.md`; the only remaining precondition is the
  CEO's space-scope choice.
- **Notes / evidence (committed; token redacted in all):**
  `evidence/deploy-prod/20261002-153548-chg003-confluence-doctor-escapehatch.txt` (doctor verdict,
  exit 1),
  `evidence/deploy-prod/20261002-153625-chg003-confluence-serve-warn-and-serve.txt` (serve WARN-and-
  serve, exit 0),
  `evidence/deploy-prod/20261002-153635-chg003-verify-tool-surface.txt` (50/50, 0 write tools),
  `evidence/deploy-prod/20261002-153733-chg003-space-discovery.txt` (space list),
  `evidence/deploy-prod/chg003_space_discovery.py` (read-only discovery driver). Not merged to main;
  not pushed; `state.json` / `plan-approval.md` / `cab-approval.md` unchanged (DM owns them).

---

## 2026-10-02T15:48:35+07:00 · prod · deploy (CHG-003 LIVE go-live — REAL EA pull, bounded first ingest) · b898040 (branch claude/zealous-johnson-yb3t2q)

> **Outcome: DEPLOYED — first REAL Confluence ingest on this feature succeeded, bounded to space EA.**
> CEO Gate 2 approved (`8-gate2/cab-approval.md` → `status: approved`, CHG-003); CTO D-008 approved the
> escape-hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (write-capable token, read-only system — 0 write
> tools). Space discovery already done (100 spaces, 2026-10-02T15:37). CEO chose scope **space key = EA**,
> bounded first pull (`--limit 20`). This entry records the real dry-run → real pull → stdio
> `kb_semantic_search` verify → smoke → 30-min watch open. **Additive ingest into the real `kb` schema**
> (prior go-lives used the real `kb` for the local demo; the EA pull is additive — the 2 Oct-1 demo rows
> are untouched). Semantic *relevance* is still **NFR-003 UNVERIFIED** (fake `fake/hashed-bow` embeddings;
> no HF model this run — HF is NOT in the egress allow-list); the stored EA rows and their citations are
> **real**. No secret appears in any evidence file (E-009 held — scanned, token absent).

- **Environment / versions:** CEO local macOS, Docker, uv 0.11.7. `DEPLOY_MODE=script`,
  `ENVIRONMENTS=uat,pre,prod`, prod = this local machine. pgvector container `mcp-dev-postgres`
  **Up (healthy)**, Postgres on host **5433** (same container/pattern as the prior go-lives).
- **CAB / authorisation:** `8-gate2/cab-approval.md` `status: approved` (CHG-mcp-data-platform-chg003-20261002,
  CEO 2026-10-02T14:57); CTO escape-hatch = **D-008** (2026-10-02T15:33, read-only-surface rests on the
  0-write-tool invariant, not on the credential). No new Gate 2 (nothing about egress/vendor/scope changed).
- **Target DB (DK1 decision):** ingested into the **REAL `kb` schema** on `mcp-dev-postgres` (host 5433,
  db `mcp_kb`). Chosen over a throwaway copy because the ingest is **additive** (upsert by source id) and
  does not touch the Oct-1 demo rows — same real `kb` the prior local go-lives used. **Pre-pull counts:**
  `kb.documents = 2`, `kb.chunks = 2` (both the Oct-1 `fake/hashed-bow` sample rows, `source_type=confluence`).
- **DK5 snapshot (BEFORE the first real write — hard pre-req, done):**
  `pg_dump` of schema `kb` (schema + data, `--no-owner --no-privileges`, restorable: 6 `CREATE TABLE` +
  6 `COPY kb.*`) →
  `evidence/deploy-prod/20261002-154355-chg003-kb-schema-snapshot.sql`
  **snapshot id (sha256) = `9e2cc595c98f552484720c42e8828c2df08161b5b442a907877134164a1d00f2`**.
  Restore if needed: `psql -U mcp_admin -d mcp_kb` after `DROP SCHEMA kb CASCADE` then replay the file.
- **Migrations:** `kb` is at **0001–0006** (`kb.schema_migrations`: 0001_extensions … 0006_review_followup);
  CHG-001's 0007/0007b/0008 are **not** on this container (CHG-001 postponed, never deployed) and **CHG-003
  adds no schema**, so the base `kb.documents`/`kb.chunks` tables are exactly what the ingest path needs.
  **Confirmed, not re-run** — no destructive migration touched.
- **Commands (exact, this session; token loaded from the git-ignored hidden file, never echoed):**
  - `set -a; source credentials/.ingest-sources.env; set +a` + `export MCP_INGEST_CONFLUENCE_TEAM_SPACES=EA`
    → scope (non-secret): `base_url=https://tnexwm.atlassian.net/wiki`, `flavor=cloud`, `spaces=EA`,
    `MCP_EGRESS_ALLOWLIST=*.atlassian.net`, `MCP_INGEST_ALLOW_LIVE_EGRESS=true`,
    `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (D-008), token_len=192 (never printed).
  - One-time dev role passwords (per the documented re-run steps; needed for host-TCP auth to the RW/RO
    roles): `ALTER ROLE mcp_query_ro/mcp_ingest_rw LOGIN PASSWORD …_dev_password` → non-destructive, exit 0.
  - **DRY-RUN** `uv run python …/chg003_ea_pull.py --dry-run --limit 20` → **exit 0, status success,
    `dry_run: true`**, `documents_seen=1 documents_upserted=1 chunks_written=25`, model `fake/hashed-bow`;
    DB counts **unchanged (2/2)** after dry-run → confirmed no write. Egress real to `*.atlassian.net`,
    read-only (GET). (evidence: `20261002-154616-chg003-dryrun-EA.txt`.)
  - **REAL PULL** `uv run python …/chg003_ea_pull.py --limit 20` → **exit 0, status success**,
    `run_id=f1d5f679-c019-4bb3-9026-7205739485f5`, `documents_upserted=1`, `chunks_written=25`,
    `cursor_advanced=true`. (evidence: `20261002-154635-chg003-realpull-EA.txt`.)
  - `uv run mcp-ingest status --json` → confluence `document_count=3 chunk_count=27 last_run_status=success
    staleness_hours=0.0 last_success_at=2026-10-02T08:46:39Z`.
  - **VERIFY** `uv run python …/chg003_ea_verify.py` (starts `mcp-pgvector` over stdio, read-only role
    `mcp_query_ro`, loopback fake-embedding) → **PASS (exit 0)** — see transcript below.
  - **SMOKE** `bash scripts/squad/smoke.sh prod` → **SMOKE PASS (prod), exit 0**.
  - **EGRESS-DENY proof** `check_egress` via the single choke point: `tnexwm.atlassian.net` → ALLOWED,
    `evil.example.com` → `EgressDenied`, `huggingface.co` → `EgressDenied` (HF correctly refused — not in
    allow-list this run). (evidence: `20261002-154819-chg003-egress-deny-proof.txt`.)
- **Real pull counts for EA:** **+1 document, +25 chunks** (document_id `1c274560-ba52-45b7-a084-5aa35ee17da4`,
  real EA page **1165426720** "Tài liệu hướng dẫn MKT config ZNS",
  `source_uri=https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/…`, `container=EA`).
  **Post-pull counts:** `kb.documents = 3`, `kb.chunks = 27` (was 2 / 2). The 2 Oct-1 demo rows intact.
- **VERIFY transcript (real JSON-RPC over stdio, read from the live DB; token/secrets redacted):**
  - `mcp-pgvector` `tools/list` → `["kb_get_document","kb_list_sources","kb_semantic_search"]` — **3
    read-only tools, no write tool** (write-capable token adds NO write capability to the surface).
  - `tools/call kb_list_sources {}` → `status: ok`, 1 source confluence (3 docs / 27 chunks), citation
    `uri=https://tnexwm.atlassian.net/`, `embedding_model=fake/hashed-bow`, `staleness_hours=0.0`.
  - `tools/call kb_semantic_search {query:"hướng dẫn MKT config ZNS", top_k:5, min_similarity:0.0}` →
    `status: ok`, returned a **real stored EA chunk**: chunk `1165426720#23`, `heading_path="11. Một số
    luồng cũ: > 11.1. ZNS Automation for Onboarding"`,
    `source_uri=https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/T+i+li+u+h+ng+d+n+MKT+config+ZNS`,
    `container=EA`, `source_updated_at=2026-10-01T04:27:46Z`, wrapped in the `<untrusted-content
    source="confluence">` guard. The content itself carried a high-entropy fragment that the **E-009
    scrub redacted** in-flight (`«redacted:high-entropy»`), so even real content with a Jira id / topic
    name does not leak secrets. (`min_similarity=0.0` used deliberately: `fake/hashed-bow` carries no
    semantic signal — NFR-003 UNVERIFIED — so retrieval is vector-nearest; the row + citation are real.)
    (evidence: `20261002-154745-chg003-verify-kb-semantic-search-EA.txt`.)
  - **This is the proof the end-to-end REAL path works:** real EA page → real ingest (egress to
    `*.atlassian.net`, read-only) → real pgvector rows → real read-only `mcp-pgvector` stdio server
    returns a real EA chunk citing `tnexwm.atlassian.net`.
- **Smoke: pass** (`scripts/squad/smoke.sh prod`): `mcp-pgvector doctor` ok (role `mcp_query_ro`,
  pgvector 0.8.6), `mcp-redis doctor` ok (ACL `mcp_ro`), `mcp-kafka doctor` ok. (evidence:
  `20261002-154756-chg003-smoke-prod.txt`.)
- **DK2 invariants held:** egress **default-deny** at the single `check_egress` choke point (unlisted
  host + huggingface.co both refused `EgressDenied`; only `*.atlassian.net` dialable); **read-only
  surface** 0 write tools (tools/list = 3 read-only); **token never leaks** — all 8 CHG-003 EA evidence
  files grepped for the raw token → **absent** (`leak_flag=0`), no E-009 regression.
- **SLI read-out (per-user stdio architecture):** error rate = **0** (ingest run status=success, 0
  errors; doctor exit 0); p95 = n/a (no network listener — SLI is liveness); `egress_denied` on
  configured host = 0 (tnexwm allowed), on unlisted host = refused as designed; staleness_hours=0.0.
  Source: ingest JSON logs + smoke + egress-deny proof.
- **Observation window:** **OPEN at 2026-10-02T15:48:35+07:00, 30 min (target close 16:18:35+07:00)** —
  baseline sample green (container healthy, pgvector doctor read-only ok, counts stable 3/27, egress-deny
  invariant holding, 0 errors). (evidence: `20261002-154835-chg003-watch-window-open.txt`.) The window is
  **running**; any rollback trigger → immediate rollback (below) + report, then a new Gate 2.
- **Rollback: n/a (not executed; nothing to roll back on a successful additive pull).** Readiness intact,
  effect **< 60 s** (`cab-pack.md` §6): (1) `unset MCP_EGRESS_ALLOWLIST` → default-deny every outbound
  host; (2) remove the Atlassian token / unset `MCP_CONFLUENCE_API_TOKEN*`; (3) stop the ingest connector
  (do not launch `mcp-ingest run`). Live egress is off whenever `MCP_INGEST_ALLOW_LIVE_EGRESS` is unset.
  To remove the ingested EA rows if ever required: `mcp-ingest prune` / truncate the EA rows (base schema
  + Oct-1 demo data untouched); restore from the DK5 snapshot above. `scripts/squad/rollback.sh prod` is
  the scripted path (local reversal, no shared service to drain).
- **Classification:** **not** an error-ledger defect — no failed smoke, no broken environment, no executed
  rollback. `errors.sh open` → **no open S1/S2** (unchanged).
- **Next step:** keep the 30-min watch to close (16:18:35+07:00); if it stays green, go-live is complete
  for the EA bounded first pull. Then (CEO's choice) widen scope to more EA pages (raise `--limit`) or add
  the next space, and — separately — open `huggingface.co` + download the real `bge-m3` model to begin
  measuring NFR-003 (fake embeddings until then). The pull remains covered by the existing CHG-003
  `cab-approval.md`.
- **Notes / evidence (committed; token redacted/absent in all):**
  `evidence/deploy-prod/20261002-154355-chg003-kb-schema-snapshot.sql` (DK5 snapshot),
  `…-154616-chg003-dryrun-EA.txt`, `…-154635-chg003-realpull-EA.txt`,
  `…-154745-chg003-verify-kb-semantic-search-EA.txt`, `…-154756-chg003-smoke-prod.txt`,
  `…-154819-chg003-egress-deny-proof.txt`, `…-154835-chg003-watch-window-open.txt`,
  plus the drivers `chg003_ea_pull.py` + `chg003_ea_verify.py`. Not merged to main; not pushed;
  `state.json` / `plan-approval.md` / `cab-approval.md` unchanged (DM owns them).

## 2026-10-02T16:00:05+07:00 · prod · deploy (post-go-live operator enablement — CLI lookup) · chg003-watch

- Commands: `scripts/kb-list.sh` (exit 0); `scripts/kb-search.sh "ZNS onboarding automation" 2` (exit 0);
  `scripts/kb-get.sh --source-uri "https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/..."` (exit 0).
- Endpoints: local `mcp-pgvector serve` over stdio against Postgres+pgvector on `127.0.0.1:5433` / db `mcp_kb`, read-only role `mcp_query_ro`.
- Smoke: n/a (no re-deploy — this sub-stage only adds the read-only lookup UX; the 30-min watch window stays as logged above).
- SLI read-out: n/a (no new serving component; lookups reuse the already-deployed read-only server).
- Rollback: n/a (additive, read-only helper scripts; no environment change).
- Notes / evidence: `evidence/deploy-prod/20261002-155928-chg003-watch-operator-lookup.txt` (all three wrappers returning real EA rows citing `tnexwm.atlassian.net/.../spaces/EA/pages/1165426720`, redacted). No secret printed; `credentials/.lookup.env` is git-ignored.

### Operator lookup — how the CEO reads the ingested Confluence content from the terminal

The CHG-003 go-live ingested the real EA page (3 documents / 27 chunks) into the local
Postgres+pgvector. You can read it back from a terminal with three read-only commands. Everything
below uses the read-only database role only; none of them needs or touches the write token, and no
password is printed.

**Setup (once):** the commands read the read-only connection string from `credentials/.lookup.env`
(a hidden, git-ignored file). It is already in place. Nothing else to configure.

**1. See what has been ingested**

```
scripts/kb-list.sh
```

Example output:

```
Nguồn đã ingest trong knowledge base:
  - confluence: 3 tài liệu, 27 chunk | model nhúng: fake/hashed-bow | cập nhật: 2026-10-02T08:46:39Z (0.2h) | trạng thái: success
    nguồn gốc: https://tnexwm.atlassian.net/
```

**2. Read a whole document (no embedding needed — exact stored text)**

```
scripts/kb-get.sh --source-uri "https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/T+i+li+u+h+ng+d+n+MKT+config+ZNS"
# or by id:
scripts/kb-get.sh --document-id 1c274560-ba52-45b7-a084-5aa35ee17da4
```

Example output (head):

```
Tài liệu: Tài liệu hướng dẫn MKT config ZNS
Nguồn: https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/...
source_id: 1165426720  |  document_id: 1c274560-ba52-45b7-a084-5aa35ee17da4
không gian (space): EA  |  số chunk: 25
--- Nội dung ---
  Khi cấu hình Link , sử dụng định dạng sau: https://go.tnex.vn/<param> ...
```

**3. Semantic search (ranked)**

```
scripts/kb-search.sh "ZNS onboarding automation" 5
```

Example output (top hit):

```
[1] Tài liệu hướng dẫn MKT config ZNS
    similarity: 0.53
    mục: 11. Một số luồng cũ: > 11.1. ZNS Automation for Onboarding
    nguồn: https://tnexwm.atlassian.net/wiki/spaces/EA/pages/1165426720/...
    Luồng tự động gửi ZNS khi KH onboard thành công tài khoản C1 ...
```

Add `--json` to any command to get the raw structured result instead of the formatted view.

**One honest caveat about ranking.** Search works today by starting the same deterministic
embedding the ingest used (model `fake/hashed-bow`), so query and stored vectors live in the same
space and the right EA chunks come back. That embedding is hashed bag-of-words — it matches on
shared words, not on meaning — so the *ranking is honest but unmeasured* (this is NFR-003, accepted
UNVERIFIED at Gate 2). The content and the citations are the real ingested EA data. `kb-list` and
`kb-get` do not depend on the embedding at all and are exact.

**Next optional step (enables measured semantic search).** Download the real model
`BAAI/bge-m3` and re-embed the corpus:

```
uv run mcp-ingest reembed --model BAAI/bge-m3
```

Then set `MCP_INGEST_EMBEDDING_PROVIDER=local` and `MCP_INGEST_EMBEDDING_MODEL=BAAI/bge-m3` in your
environment and drop the fake-endpoint shim inside `scripts/kb-search.sh` (one `needs_embedding`
branch in `scripts/_kb_mcp.py`). After that, `kb-search.sh` ranks on real semantics and a recall
number can be measured against NFR-003.

---

## 2026-10-02T22:25:44+07:00 · prod · watch (đóng cửa sổ theo dõi — GREEN) · b898040 (branch claude/zealous-johnson-yb3t2q)

> **Kết quả: cửa sổ theo dõi đóng ở trạng thái XANH. Không có trigger rollback nào bị vi phạm.
> KHÔNG cần rollback.** Cửa sổ 30 phút mở lúc 15:48:35, hạn đóng 16:18:35; giờ là ~22:25, đã quá
> hạn từ lâu và trong khoảng đó hệ thống chạy ổn (có cả lần đo NFR-003 thật lúc 22:03). Đây là lần
> đọc xác nhận cuối cùng trước khi chốt đóng. CEO chọn Option 1: đóng watch GREEN và giữ NFR-003 ở
> mức 'đã đo lần đầu (cỡ mẫu nhỏ)'.

- **Cửa sổ:** mở `2026-10-02T15:48:35+07:00` → hạn đóng `16:18:35`; đóng xác nhận
  `2026-10-02T22:25:44+07:00`. prod = máy local của CEO (per-user stdio, không có service dùng chung).
- **Commands (lần đọc cuối, chính xác):**
  - `date "+%Y-%m-%dT%H:%M:%S%z"` → `2026-10-02T22:25:44+0700`.
  - `docker ps --filter name=mcp-dev-postgres` → `Up 28 hours (healthy)` (exit 0).
  - `psql kb` đếm dòng → `documents=3`, `chunks=27`; phân bố model → `BAAI/bge-m3 | 27` (exit 0).
  - `check_egress`/`host_allowed` qua `mcp_common.egress` → allowlist rỗng chặn tất cả;
    `*.atlassian.net`: `tnexwm.atlassian.net` ALLOWED, `huggingface.co` + `evil.example.com` DENIED (exit 0).
  - `git check-ignore .token-key` → `.token-key` (gitignored); `git ls-files` → không file bí mật nào bị track.
  - `psql kb` đọc `kb.ingest_runs` → confluence run gần nhất `status=success`, 0 lỗi (exit 0).
- **Năm trigger rollback (D-007 DK2) — kiểm lần cuối, tất cả XANH:**
  1. **Liveness:** container `mcp-dev-postgres` Up 28h (healthy); db `mcp_kb`, pgvector 0.8.6, role
     read-only `mcp_query_ro`. **XANH.**
  2. **Số dòng (SLI):** `documents=3`, `chunks=27`, ổn định. Cả **27/27 chunk mang model
     `BAAI/bge-m3`** sau lần re-embed NFR-003 lúc 22:03 — đây là **trạng thái ĐÚNG sau re-embed**
     (half-migration guard sạch), **không phải trôi dữ liệu**; số dòng không đổi so với trước re-embed.
     **XANH.**
  3. **Egress chặn-mặc-định:** allowlist rỗng = từ chối mọi host; với `*.atlassian.net` thì
     `tnexwm.atlassian.net` được phép, `huggingface.co` + `evil.example.com` bị từ chối, qua một choke
     point duy nhất. **XANH.**
  4. **Token không rò:** `.token-key` đã gitignore và không được track; quét toàn bộ file commit +
     evidence không thấy giá trị token (`leak_flag=0`). (E-009 scrub wiring đã verified-closed trước đó.)
     **XANH.**
  5. **Trạng thái ingest (error rate):** lần chạy confluence gần nhất = `success`, **0 lỗi**. **XANH.**
- **Endpoints:** không có listener mạng (per-user stdio). Backing store: Postgres+pgvector trên
  `127.0.0.1:5433`, db `mcp_kb`.
- **Smoke: pass.** Smoke prod đã chạy xanh ở lần go-live 15:48 (`mcp-pgvector`/`mcp-redis`/`mcp-kafka`
  doctor ok); lần đóng này đọc lại trực tiếp liveness + read-only gate (`mcp-dev-postgres` healthy,
  `mcp_query_ro` ok) và cả 5 trigger — không trigger nào bị vi phạm trong suốt cửa sổ.
- **SLI read-out:** error rate = **0** (mọi confluence run `success`, doctor read-only ok); p95 = n/a
  (per-user stdio, không có listener — SLI là liveness); `egress_denied` trên host cấu hình = 0, trên
  host không được cấu hình = bị từ chối đúng thiết kế; staleness ~0.
- **NFR-003 — đo lần đầu (cỡ mẫu nhỏ, chưa hiệu chỉnh):** sau khi tải xong model `BAAI/bge-m3` (nạp
  offline), đã re-embed thật 27/27 chunk và đo NFR-003 lần đầu trên **1 tài liệu EA thật (25 chunk):
  hit@5 = 8/8, MRR = 1.0, `calibration_status=uncalibrated`**. Đây là **tín hiệu tích cực nhưng cỡ
  mẫu nhỏ** — KHÔNG suy rộng ra chất lượng trên toàn corpus (giữ đúng L-002: không thổi phồng một phép
  đo chưa đủ dữ liệu). Theo Option 1 của CEO, NFR-003 giữ ở mức **'đã đo lần đầu (cỡ mẫu nhỏ)'**, chưa
  phải **'verified trên dữ liệu công ty'** (cần corpus EA lớn hơn + golden-set đã kiểm chứng + hiệu
  chỉnh ngưỡng τ).
- **Rollback: n/a — KHÔNG kích hoạt, không có gì để rollback** (cửa sổ xanh suốt, pull additive thành
  công). Khả năng rollback vẫn sẵn, hiệu lực **< 60 s** (`cab-pack.md` §6): bỏ
  `MCP_EGRESS_ALLOWLIST` → chặn mọi egress; gỡ token; dừng connector ingest.
- **Phân loại:** **không** phải lỗi trong error ledger — không smoke fail, không phá môi trường, không
  rollback. `errors.sh open` → vẫn **không có S1/S2 mở**.
- **Notes / evidence (committed; token redacted/absent):**
  `evidence/deploy-prod/20261002-222544-chg003-watch-close-sample.txt` (số liệu đọc trực tiếp của 5
  trigger), `evidence/deploy-prod/20261002-222544-chg003-watch-close-summary.md` (tóm tắt cho CEO),
  cùng các evidence NFR-003 lúc 22:0x (`…-220657-…-measurement.json`, `…-221147-…-invariants.txt`,
  `…-221200-…-nfr003-report.md`). Chưa merge, chưa push; `state.json` / `plan-approval.md` /
  `cab-approval.md` không đụng (DM sở hữu).
