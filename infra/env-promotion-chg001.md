# CHG-001 — environment promotion additions (DK3, T-089)

> Extends `squad-env-promotion` (the base dev → UAT → PRE → CAB → PROD → watch path) with the
> gates CHG-001 adds. Scope: Company Knowledge tier (Option C, ADR-0017) + B4 grounding. This doc
> is backend-owned (`infra/`); the base env-promotion skill is unchanged.

## DK3 — Redis ACL is least-privilege **per environment** (never reuse the dev ACL)

**Why this is a gate, not a convention.** The dev Redis in `infra/redis/users.acl` ships two broad
grants that exist only to make local development frictionless:

- `user default on nopass ~* &* +@all` — the default user has **no password** and **every command**
  on **every key**.
- `user mcp_ro on >mcp_ro_dev_password ~* …` — a hard-coded dev password and `~*` (all keys).

These are acceptable on a laptop with a throw-away container. They are **not** acceptable on any
shared/staging/pre-prod/prod Redis, and the dev ACL file **must not be copied forward** (R-013).
CHG-001 does not add a Redis-backed component with new write needs, but the gateway rate-limit and
any future shared cache would land on Redis, so the rule is pinned here before that happens.

**Promotion checklist — Redis ACL least-privilege per env.** Before promoting past `dev`:

1. The `default` user is **disabled or password-protected** (`user default off`, or `on` with a
   per-env secret and a narrowed command set) — never `nopass +@all` outside dev.
2. Every application user has a **per-env secret** supplied from the env's secret store
   (`>…_FILE` / vault), never the literal `mcp_ro_dev_password` from the dev file.
3. Key patterns are **scoped** (`~mcp:*` or tighter), not `~*`, for every non-admin user.
4. The read-only user keeps exactly the read + introspection commands it needs
   (`+get +mget +scan +type +ttl … +acl|whoami +acl|getuser +select`) and **no write category**
   (`-@write` holds). The `mcp-redis` startup credential gate already asserts "no write category"
   via `ACL GETUSER` (ADR-0008 A1/A5) — that gate runs in **every** environment, so a mistakenly
   broad shared ACL is caught at serve time, not in production.
5. The dev ACL file (`infra/redis/users.acl`) is referenced **only** by `infra/docker-compose.yml`
   and is never mounted into a shared environment.

## New domain credentials (DK3) — read via `mcp_query_ro`, write via `mcp_ingest_rw`

The four CHG-001 knowledge domains (`kb.document_versions`, `kb.entities` + `kb.relationships`,
`kb.knowledge_summaries`, `kb.document_permissions`) and the source-authority config tables
(`kb.source_authority`, `kb.confidence_weights`, `kb.freshness_horizon`) live in the **same**
schema `kb`. Migration `0007`/`0008` grant:

- `SELECT` to **`mcp_query_ro`** — the Live/Knowledge read path (Knowledge MCP, `mcp-pgvector`);
- `SELECT, INSERT, UPDATE, DELETE` to **`mcp_ingest_rw`** — the ingest write path only.

No new role is introduced and no credential is reused from Redis or elsewhere. The Knowledge MCP
DSN (`MCP_KNOWLEDGE_DSN`) **must** be `mcp_query_ro`; the startup gate refuses to serve on a
write-capable DSN exactly as `mcp-pgvector` does (ADR-0003 A1).

## Migration-locking gate (DK2) at each promotion

Because the CHG-001 schema changes run on a pgvector store that is already go-live with data
(R-006/R-007), the following must hold before promoting the migration past `dev`:

- `mcp-ingest db status --json` lists `0007_knowledge_domains`, `0007b_knowledge_indexes` and
  `0008_source_authority` under `applied` (T-090).
- The `*.concurrently.sql` migration ran **outside** a transaction and left **no `INVALID`**
  index behind.
- Observed lock wait during `db upgrade` on a populated copy stayed under `lock_timeout` (3s); the
  read path was not blocked (verified in `test_db_upgrade_chg001.py` / `test_migration_locking.py`).
- A backup/snapshot of the `kb` database was taken immediately before applying on PRE/PROD
  (cab-pack §5).

## New per-environment variables (see `.env.example`, CHG-001 block)

| Variable | Rule across environments |
|---|---|
| `HF_HUB_OFFLINE` | `1` in **every** environment (no egress to the HF hub). Never 0 outside an explicit, logged, offline-model-fetch maintenance step. |
| `MCP_RERANKER_MODEL_PATH` | Per-env absolute path to local weights. Missing ⇒ RRF-only fallback with `reranker=disabled` reported (not a silent degrade). |
| `MCP_KNOWLEDGE_DSN` | `mcp_query_ro` only; per-env secret from the env's store. |
| `MCP_JIRA_TOKEN` | Read-only token/PAT, per-env secret; must not create/transition/comment. |
| `MCP_GATEWAY_*` | In-process only; no listening socket is ever opened (NFR-005). |
