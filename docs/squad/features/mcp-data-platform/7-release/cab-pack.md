# mcp-data-platform — Change Request for Go-live

| Field | Value |
|---|---|
| Change ID | CHG-mcp-data-platform-20261001 |
| Version | `0363fcc` (branch `claude/zealous-johnson-yb3t2q`; tag at go-live `release/mcp-data-platform-20261001`) |
| Type | normal |
| Risk rating | medium (medium impact × low likelihood — from the matrix below) |
| Proposed window | 2026-10-02, 10:00–11:00 +07:00 (1 h), low-traffic; observation window 30 min after cut-over |
| CTO recommendation | READY_FOR_CAB — decisions.md D-002 (D3 cab-readiness; basis: `6-verify/review-report.md` round-2 verdict **APPROVE**, 0 CRITICAL / 0 open HIGH). *This CAB pack is a document for CEO Gate 2 approval; it performs no deploy.* |

> **CEO Gate 2 is required for the four caveats in §9.** Caveat 1 (NFR-003 semantic quality UNVERIFIED)
> needs an explicit PO/CEO risk-acceptance. Under `DEPLOY_MODE=script`, the production step is run by
> hand as `SQUAD_CAB_APPROVAL=…/8-gate2/cab-approval.md scripts/squad/deploy.sh prod` and the CEO
> confirms the Kiro permission prompt at run time — nothing is deployed by this document.

## 1. What changes and why

Nine read-only MCP servers (Confluence, GitLab, OpenSearch, Kibana, CloudWatch, Kafka, Redis, SQS/SNS,
Postgres+pgvector) plus an ingest/embedding pipeline that crawls sources, embeds, and writes to
`kb.*` in Postgres+pgvector. Each server runs **local, per-user, over stdio** inside Claude
Desktop/Code — there is no shared service, no network listener, no UI (plan: `5-plan/implementation-plan.md`;
discovery: `1-discovery/product-requirement.md`). Business value: one read-only query surface over the
team's technical/operational sources, cutting the manual tab-switching and incident-investigation time
called out in discovery. **Read-only is absolute** across all nine sources (NFR-001).

## 2. Scope

- **Delivered (all Must):** FR-001…FR-015. 9 MCP servers (49 read-only tools, verified against
  `api-contract.yaml` — 49/49 tool names + schemas match) + `mcp-ingest` CLI (6 commands) + the
  ingest/embedding pipeline into `kb` (schema via migrations 0001–0006).
- **Deferred / explicitly not in v1:** retrieval-quality rerank + lexical fallback (R-014), a
  relevance/faithfulness eval harness (R-015), and the real-model NFR-003 measurement — see §9 caveat 1.
  Non-goals (unchanged): no UI, no write path, no multi-user/remote HTTP+SSE hosting.

## 3. Evidence

- UAT: **n/a** — the architecture has no shared UAT/PRE service; servers are per-user local stdio
  (`4-design/architecture.md` → Environments & deployment). The dev checkpoint leads straight to
  go-live; "deploy" = each user adding the server snippet to their Claude Desktop config and starting
  the ingest scheduler.
- PRE: **n/a as a shared environment** (same reason). PRE-equivalent verification was performed on the
  local dev host with real services (see dev regression below). No shared-PRE rollback rehearsal was run
  because there is no shared PRE server to roll back; the real rollback timing is in §6.
- Dev regression: `6-verify/regression-report-dev.md` — **1846 passed, 174 skipped, 0 failed** at
  `0363fcc`; the 10 P1 pgvector E2E tests executed first-ever on real PostgreSQL 16.15 + pgvector 0.8.6
  (**10/10 pass**; full `e2e/` **34/34, 0 skipped**); BE coverage **89.97%** on changed code; recall
  (ANN-vs-brute-force) gate pass. Logs: `evidence/qa-dev/20261001-170716-*`.
- Review: `6-verify/review-report.md` round 2 — **APPROVE**, **0 CRITICAL, 0 open HIGH**, 12 MEDIUM +
  10 LOW all non-blocking/deferred. Round-1 blocking R-001…R-005 all resolved and verified.
- Defects: `records/errors.md` — **4 found, 4 closed** (E-001…E-004, each with a `· verified` record);
  **open: none S1/S2** (`errors.sh open` → none). Deferred residual R-006…R-027 are in §9.
- Production audit: not run as a server audit (no shared/hosted service to audit). Read-only surface
  audit stands in: `scripts/verify_tool_surface.py` → **42/42 checks passed** (read-only surface,
  prompts, 6 CLI commands, ingest write-credential never present in any server env);
  `scripts/validate_contract.py` → OK.

## 4. Impact

- **Users/systems affected:** opt-in, per user — only users who add a server to their own Claude
  Desktop/Code config. No shared service, so no blast radius beyond the adopting user's local process.
- **Downtime:** **0** (nothing shared is replaced; adoption is additive and per-user).
- **Data changes:** the ingest pipeline creates/populates schema `kb` in Postgres+pgvector via
  migrations 0001–0006. **First deploy runs them on empty tables.** Migrations are **expand-only**
  (no destructive down-migration needed); `kb` data is reversible via `mcp-ingest prune` / truncate.
  The nine source systems are touched **read-only only** — no writes anywhere upstream.

## 5. Deployment plan

Ordered, `DEPLOY_MODE=script`, run by hand (CEO confirms the Kiro permission prompt on the prod step):

1. Tag `release/mcp-data-platform-20261001` at `0363fcc`.
2. **Backup check (data migration present):** confirm a restorable snapshot of the target Postgres
   exists and log its id before step 3. (First deploy is onto empty `kb`, so the backup protects the
   wider DB, not `kb` data.)
3. `MCP_INGEST_ADMIN_DSN=… uv run mcp-ingest db upgrade` on the target Postgres (applies 0001–0006).
4. `SQUAD_CAB_APPROVAL=docs/squad/features/mcp-data-platform/8-gate2/cab-approval.md scripts/squad/deploy.sh prod`
   (refuses unless `cab-approval.md` has `status: approved`; CEO confirms the permission prompt).
5. `scripts/squad/smoke.sh prod` — `uv run mcp-<server> doctor` for the 9 servers + read-only surface.
6. Users add the emitted `config-emit` snippet to Claude Desktop; enable the ingest scheduler
   (`infra/scheduler/run-ingest.sh`, cron/launchd).

Duration: ~15–30 min (migration + smoke); user adoption is rolling and per-user.

## 6. Rollback plan

- **Triggers:** smoke fails on prod (`doctor` red for any server); ingest error-rate above baseline × 2
  for 5 min; a P1 journey (FR-003 / FR-009 / FR-013) fails a canary check. Any trigger → roll back
  immediately, no approval needed.
- **Steps:** `scripts/squad/rollback.sh prod` → remove the server(s) from the Claude Desktop config and
  stop the ingest scheduler; verify with `scripts/squad/smoke.sh prod`; `scripts/squad/notify.sh rollback "<trigger>"`.
- **Measured time-to-rollback:** **< 60 seconds** — code rollback is removing the per-user stdio server
  entry (effect is immediate: the process is simply not launched) and stopping the scheduler; no shared
  service to drain. A shared-environment rehearsal was **not applicable** (no shared PRE/PROD service in
  this architecture); the figure is the real cost of the documented rollback step, not a rehearsal.
- **Data rollback:** migrations are expand-only, so reverting code needs no down-migration; `kb` data is
  cleaned with `mcp-ingest prune` or `truncate kb.*`. Upstream sources are read-only — no upstream data
  to reverse.

## 7. Post-go-live verification

- **Smoke:** `scripts/squad/smoke.sh prod` — `uv run mcp-<server> doctor` (liveness = process + tool
  registration; readiness = startup credential/read-only check) for all 9 servers, plus the read-only
  surface check; 2–3 P1 journeys (FR-003 Confluence/GitLab read, FR-009 observability read, FR-013
  semantic search).
- **Observation window:** **30 minutes** (per `squad-observability`), SLIs read at start (baseline)
  then every 5 minutes.
- **SLI read-out command:** error rate = `count(status=error)/count(all)` by `tool`, p95 =
  `p95(duration_ms)`, computed from the JSON stderr logs in `MCP_LOG_FILE` over the last N minutes
  (`4-design/architecture.md` → Observability). Each `squad-env-promotion` rollback trigger maps to one
  of these SLIs (doctor-fail → readiness; error-rate → status=error ratio; p95 → latency).
- **Success criteria:** smoke passes and no rollback trigger fires during the window.

## 8. Communication

- **Before:** CEO (Gate 2 approval) and the adopting team notified via chat (`NOTIFY_CHANNELS=chat`),
  with the "What changes" summary above and the config-emit snippet.
- **After:** `scripts/squad/notify.sh deployed "<version>"` on success; `notify.sh rollback "<trigger>"`
  on any rollback. Channel: chat.

## 9. Residual risks (accepted MEDIUM/LOW + Gate-C caveats requiring CEO acceptance)

### 9.0 Caveats the CEO must explicitly accept at Gate 2

1. **NFR-003 semantic retrieval quality is UNVERIFIED (R-004).** The reported "recall ≥ 0.95" measures
   only **ANN-index correctness** (HNSW returns the same rows as brute-force under
   `DeterministicFakeProvider`), which yields ~1.0 even if the embedding were noise — it is **not**
   semantic relevance. The real measurement needs a chosen embedding model (ADR-0010 still `proposed`;
   `bge-m3` PROVISIONAL) and a `--provider configured` run, both **blocked by Hugging Face egress
   (403)**. The claim has been correctly relabelled across ADR-0011 A3 / architecture.md / signoff;
   the gap is documented, not an error. **Requires explicit PO/CEO risk-acceptance** that v1 ships with
   retrieval quality unproven (coupled with R-014/R-015 below).
2. **153 package-level Postgres integration tests skip on non-Debian hosts (R-005 secondary).**
   `packages/conftest.py::_find_pg_bin()` probes only `/usr/lib/postgresql/*/bin/initdb`, so the
   package-level `pg_server` fixture skips on macOS / any non-Debian CI. **The same behaviours are
   covered end-to-end against real pgvector** via the `e2e/` `MCP_E2E_PG_URL` override (10/10 P1 pass),
   so behaviour is verified; only the package fixture is non-portable. Owner backend; small follow-up to
   honour `PATH`/an env override.
3. **CI is not enforced by a runner (R-016).** The 5 verification gates (lint, typecheck, coverage,
   `validate_contract`, read-only suite) run via `make ci` **on demand**; no CI runner or pre-commit
   hook blocks a merge that skips them. Gate-C sign-off is on **manually-run** gates, not
   runner-enforced ones.
4. **Migration-locking is safe for the first deploy only (R-006/R-007).** The `CHECK` lacks `NOT VALID`
   and `CREATE INDEX CONCURRENTLY` cannot run inside the per-file transaction — **latent**: all six
   migrations ship in one commit onto empty tables, so no live cluster sits mid-version with real data.
   **Must be revisited before the next schema change against populated prod data.**
5. **Live-source checks are a manual sign-off checklist (not automatable here).** Confluence/GitLab real
   API shape (restrictions/permissions, which the `visibility` label depends on), `doctor` against live
   sources, Claude Desktop registration, and a real `eval` run all need real VPN/credentials.
   Tracked as a manual Gate-C checklist in `evidence/legacy/SESSION-HANDOFF.md` and
   `docs/signoff/phase-{1,2,3}.md`.

### 9.1 Accepted deferred MEDIUM (R-006…R-017) — v1 residual risk (review-report §2)

- **R-006, R-007** — migration-locking hazards (latent; empty-table first deploy). See caveat 4.
- **R-008, R-009, R-010** — missing freshness/`source_uri` indexes; row-by-row ingest; no
  `statement_timeout` on `mcp_ingest_rw`. Performance/operability at scale; correctness holds; local v1
  corpus is small.
- **R-011** — `vector(1024)` hardcoded while ADR-0010 is `proposed` (both S2 candidates are 1024d → safe
  today; `reembed` guards dim-mismatch). Tie a rewrite runbook to ADR-0010 finalization.
- **R-012** — contract/impl drift: `prune` makes `--older-than` unconditionally required vs the
  contract's `anyOf`. Drift is in the **safe** direction; reconcile in a follow-up (widen impl or SA
  narrows contract).
- **R-013** — Redis ACL `mcp_ro` grants unbounded reads + `default nopass +@all` on the dev compose.
  **Not reachable** via any tool (`enforce()` gates every command). **Must never be reused for
  staging/shared.**
- **R-014, R-015** — no rerank/lexical fallback; no relevance/faithfulness harness. Declared deferrals;
  same family as caveat 1 (retrieval quality unverified for v1), PO-owned scope.
- **R-016, R-017** — CI documentation-only (caveat 3); mypy not strict. Governance/signal strength, not
  product defects.

### 9.2 Accepted deferred LOW (R-018…R-027) — v1 residual risk

Dead `with_tool_deadline` (R-018), block-attempts off-by-one (R-019), missing `aclose()` in `serve()`
(R-021), sync calls in the one-shot benchmark (R-022), `*_FILE` mode not checked (R-023), ATX-heading
over-detection (R-024), missing `REVOKE/ALTER DEFAULT PRIVILEGES` (R-020), dead doc links (R-025),
untracked TC ids (R-026), committed squad backup dir (R-027). All quality items; R-025 and R-027 are
quick wins worth doing opportunistically. None reachable as a live safety or data-loss defect.

### 9.3 Open error ledger entries (S3/S4)

**None.** All four ledger entries (E-001…E-004) are **closed and verified**; `errors.sh open` reports
no open S1/S2. The residual risks above are review findings (R-ids) accepted as deferred, not open
defects.

## 9a. Baseline check

- **Scope:** as approved at Gate 1 (CHG-001, deviation 100/100 — a new platform; decisions.md D-001).
  Delivered FR-001…FR-015 unchanged; deferrals in §2/§9 are quality/measurement items, not scope cuts.
  **Delta vs plan: 0% scope reduction.**
- **Cost / schedule:** within the Gate-1 plan envelope; no CTO decision moved cost/schedule beyond the
  `ESCALATE_COST_PCT=15` / `ESCALATE_SCHEDULE_PCT=20` thresholds (no such escalation recorded after
  D-001).
- **CTO decisions that changed the baseline:** none beyond D-001 (sizing/escalation). The round-1 → 2
  fixes were defect remediation, not baseline change.

## 10. CEO decision

Filled by the orchestrator in `docs/squad/features/mcp-data-platform/8-gate2/cab-approval.md`, not here.
