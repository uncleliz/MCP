# mcp-data-platform — Change Request for Go-live (CHG-003: real ingestion + real egress)

> **This pack supersedes the CHG-001 Company Knowledge pack and the 2026-10-01 9-source pack.** Both
> prior packs are kept verbatim in the appendices at the bottom of this file (for the rollback pattern
> and the baseline go-live record), and the prior go-lives stay recorded in `7-release/release-log.md`
> — none of that history is modified. This is the **third plan baseline** on the same feature.
>
> **CHG-001 (Company Knowledge) is POSTPONED, not in this go-live.** The CEO deferred CHG-001 at its
> own Gate 2 (no `cab-approval.md` was written for it; state 2026-10-02T10:00). Its code is built and
> reviewed and stays on disk, but it is not what this change ships. CHG-003 ships the real-ingestion /
> real-egress enablement on the live 9-source base, driven from the CLI, Confluence first.

| Field | Value |
|---|---|
| Change ID | CHG-mcp-data-platform-20261002-chg003 (CHG-003 real ingestion + real egress) |
| Version | CHG-003 slice on base `b898040` (branch `claude/zealous-johnson-yb3t2q`); tag at go-live `release/mcp-data-platform-chg003-20261002` cut from the committed CHG-003 commit |
| Type | normal |
| Risk rating | medium (medium impact × low likelihood — from the matrix below) |
| Proposed window | 2026-10-02, 15:00–16:00 +07:00 (1 h), low-traffic; observation window 30 min after cut-over |
| CTO recommendation | pending D3 cab-readiness (set by the orchestrator from `6-verify/review-report.md` CHG-003 round-1 verdict **APPROVE**, 0 critical / 0 high / 1 medium / 1 low, both non-blocking residuals). *This CAB pack is a document for CEO Gate 2 approval; it performs no deploy and opens no egress.* |

> **CEO Gate 2 is required.** This go-live turns on the **first real outbound network and the first
> real external vendor** for this system, so the production (local) step and the first real egress
> both need the CEO's confirmation at run time. Under `DEPLOY_MODE=script` the production step is run
> by hand as
> `SQUAD_CAB_APPROVAL=…/8-gate2/cab-approval.md scripts/squad/deploy.sh prod` and the CEO confirms the
> permission prompt. The real egress is a **separate** operator action gated behind
> `MCP_INGEST_ALLOW_LIVE_EGRESS=true` plus a real read-only Atlassian token the CEO supplies at run
> time — the deploy script does not pull from a real tenant on its own. Nothing is deployed or egressed
> by this document, and the production script refuses to run until a **new** CHG-003 `cab-approval.md`
> with `status: approved` exists (the orchestrator writes it after Gate 2; no prior approval covers
> this change). The five residual risks in §9.0 need explicit CEO/PO acceptance.

## 1. What changes and why

CHG-003 removes the stub. Everything shipped so far ran under **no-egress** and **vendors=none**, with
real sources faked (a demo HTTP stub for Confluence, a deterministic fake embedding provider,
Hugging Face returning 403). This change opens **real outbound network for the ingest-pull path only**
to `*.atlassian.net` (Confluence Cloud `https://tnexwm.atlassian.net` first) plus a one-time
`huggingface.co` embedding-model download, accepts **Atlassian as a vendor** with a stored
**read-only API token**, and ships a **CLI-driven runbook for all 9 sources** (4 ingestable, 5
live-only), Confluence first.

Business value: the system stops being a demo. It ingests the company's real Confluence content and
answers from it, the CEO drives it entirely from the CLI, and the one-time model download makes the
semantic-quality measurement (NFR-003) possible for the first time. The change is a configuration /
operations boundary flip, not a code redesign — the connector, ingest pipeline, pgvector store and
read-only + stdio surface all stay exactly as built (ADR-0023; Gate-1 plan `2-gate1/plan-approval.md`
CHG-003 Option B; CTO sizing D-006).

## 2. Scope

- **Delivered (CHG-003, FR-023…FR-027 — 4 Must + 1 Should, 17 new AC):**
  - **Real egress for the ingest-pull path**, default-deny single choke point (`mcp_common/egress.py`):
    allow-list = the configured source hosts and nothing else; `*.atlassian.net` first; GitLab /
    OpenSearch / Jira hosts only as configured; any unlisted host refused.
  - **A one-time `huggingface.co` model download** (separable from Atlassian egress), `HF_HUB_OFFLINE`
    flips online only for the download then back to offline for serving.
  - **Atlassian as a vendor** + a stored **least-privilege read-only API token** (env / `*_FILE` only,
    never committed / logged, `scrub()` both ways); `doctor` refuses a write-capable account.
  - **A CLI runbook for all 9 sources** (architecture.md Appendix A): 4 ingestable (Confluence,
    GitLab, OpenSearch, Jira — doctor → `mcp-ingest run` → `status` → verify via `kb_semantic_search`)
    + 5 live-only (CloudWatch, Kibana, Kafka, Redis, SQS/SNS — doctor → register → `tools/list`; never
    ingested).
- **Invariants KEPT (not relaxed — a condition of go-live, adversarially tested per ADR-0023 §6e):**
  the 9 MCP servers + Jira stay **read-only-to-source + stdio** (NFR-005), no new network port; **0
  write tools** across all 11 servers (62 tools == contract); egress is **default-deny at a single
  choke point**; the token is scrubbed both ways; the **CHG-001 permission choke point #1 and
  grounding gate #2 are unchanged** (they ship with the code but the Company Knowledge layer is not
  this go-live — see the note below).
- **Explicitly NOT in this go-live:** **CHG-001 Company Knowledge is POSTPONED** — the Knowledge/Jira
  business tools and the grounding surface are built and reviewed but are not what the CEO is turning
  on here. The deferrals in §9.0 (real recall measurement, calibrated τ, provider=http hardening) are
  residual risks, not scope cuts. Non-goals unchanged: no UI, no write path, no multi-user HTTP+SSE.

## 3. Evidence

- UAT: **n/a** — the architecture has no shared UAT/PRE service; the servers are per-user local stdio.
  (`ENVIRONMENTS` carries no shared UAT/PRE for this feature.) The dev checkpoint leads straight to
  go-live.
- PRE: **n/a as a shared environment** (same reason). PRE-equivalent verification ran on a real dev
  host with Docker — PostgreSQL 16.15 + pgvector 0.8.6 in container `mcp-dev-postgres` on
  `127.0.0.1:5433`, the live `mcp_kb` left untouched (throw-away DB `t_<uuid>` dropped at teardown).
  No shared-PRE rollback rehearsal is possible (no shared service to roll back); real rollback timing
  is in §6.
- Dev regression: `6-verify/regression-report-dev.md` (CHG-003, env dev) — `make ci` FULL **exit 0**,
  **2344 passed / 0 failed / 206 reasoned skips**, coverage **90.08% TOTAL** (every changed package
  ≥ 80%; `egress.py` 98%, `redact.py` 94%), `validate-contract` OK, read-only suite **215 passed**. QA
  re-ran independently and did not trust the backend sign-off. Logs: `evidence/qa-dev/20261002-12*`.
  - `scripts/verify_tool_surface.py` → **50/50 checks**: `registered=62 contract=62 diff=[]`;
    **0 write tools × 11 servers** (Jira 0 write); unknown-write tool rejected at the JSON-RPC layer.
  - **The four ADR-0023 §6e adversarial / invariant tests ALL HOLD:**
    - **#1 egress default-deny** (TC-113): an unlisted host is **refused** (`EgressDenied`) and **no
      socket** is opened to it; empty allow-list ⇒ deny-all.
    - **#2 allow-list fail-closed, single choke point** (TC-114 / TC-111 / TC-112): a configured host
      is reached, an unconfigured host **fails closed before dial**, exactly one `check_egress` choke
      point, **no bypass**.
    - **#3 token-never-leak + E-009 production wiring** (TC-115 + `test_token_scrub_wiring.py`): a
      forced error on the credential path scrubs the token from the result **and** stderr both ways;
      and `register_secret` / `register_dsn_secret` is **actually called at all 11 client
      constructors**, so an **opaque** token (no shape, low entropy) scrubs in production — not only
      in the mechanism unit test. 14 passed.
    - **#4 servers read-only + stdio, 0 new port** (TC-125 / TC-126): the 9 servers + Jira keep stdio,
      open no listening socket, make no outbound call beyond their own upstream; `doctor` refuses a
      write-capable Atlassian account (TC-116).
  - **Confluence Cloud e2e on real pgvector** (TC-118): doctor → `mcp-ingest run --source confluence`
    → `status --json` → `kb_semantic_search`, citation `source_uri` resolving to `tnexwm.atlassian.net`
    (fixtures + fake token); generalised to GitLab / Jira / OpenSearch (TC-122 / TC-123); 9/10 pass,
    TC-121 (`@live`) correctly skipped.
  - **Permission regression on real-ingested restricted content** (TC-124, real pgvector,
    `MCP_LIVE_TESTS=1`): a restricted real-ingested Confluence doc is blocked at ingest and is never a
    candidate / in the pack / cited — **3/3** (E-007 not weakened).
  - **Model egress separable + offline serving** (TC-129): HF-only on the model path, `HF_HUB_OFFLINE`
    flip/restore, **0 socket** during serving; TC-130 NFR-003 conditional reports a verdict
    distribution with `calibration_status=uncalibrated`, **recall `None`**, no invented recall/τ.
- Review: `6-verify/review-report.md` CHG-003 round 1 — **APPROVE, 0 critical / 0 high**; 1 MEDIUM
  (R-C3-001, non-blocking residual) + 1 LOW (R-C3-002). The reviewer independently reproduced the
  egress choke point at exactly three seams, the token-scrub wiring at all 11 constructors, and
  `verify_tool_surface` 50/50.
- Defects: `records/errors.md` via `scripts/squad/errors.sh list --feature mcp-data-platform` —
  **9 found, 8 closed, 1 open (accepted)**. **E-mcp-data-platform-009** (S2 security, token-scrub not
  wired in production) is **verified-closed**. **E-mcp-data-platform-008** (S3 test) is the only open
  entry — a pre-existing base-test-plan gap (Must FR-005 / Kibana has no dedicated E2E TC),
  CTO-deferred, non-blocking, declared as an accepted-open residual in §9.2. The base + CHG-001
  defects E-001…E-007 are all closed/verified. **0 open S1/S2.**
- Production audit: not run as a server audit (no hosted service). The read-only surface audit stands
  in: `verify_tool_surface.py` **50/50** + `validate_contract.py` OK, both re-run by QA this session.

## 4. Impact

- **Users/systems affected:** the CEO's local machine (prod = local, per-user stdio). The change is a
  policy/operations flip; no shared service, no blast radius beyond the local process. For the first
  time the system reaches an external SaaS over the public internet — Atlassian Cloud
  (`tnexwm.atlassian.net`) read-only, and `huggingface.co` once for the model.
- **Downtime:** **0** — adoption is additive and local; nothing shared is replaced.
- **Data changes:** **none from CHG-003** — it adds **no new schema** (DK2 was done in CHG-001 E1; the
  knowledge migrations 0007/0007b/0008 are not part of this change). The only data effect is that
  `mcp-ingest run` writes **real** Confluence content into `kb.*` under `mcp_ingest_rw` where before it
  wrote stub content — the schema is unchanged and the write path is the one already shipped. Expand-
  only; the ten source systems (incl. Jira) are touched **read-only only**. Because real content now
  enters the corpus, confirm a restorable snapshot of the target Postgres exists before the first real
  pull (§5) and log its id.

## 5. Deployment plan

Ordered, `DEPLOY_MODE=script`, run by hand on the CEO's local machine (prod = local, per-user stdio);
the CEO confirms the permission prompt on the prod step **and** supplies a real read-only Atlassian
token at run time for the first real egress:

1. Commit the CHG-003 build slice and tag `release/mcp-data-platform-chg003-20261002`.
2. **Backup check (real content now enters the corpus):** confirm a restorable snapshot of the target
   Postgres exists and **log its id** before the first real pull (a `pg_dump` of schema `kb` or a
   volume copy; CHG-003 adds no migration, so this guards the ingested rows, not a schema change).
3. `SQUAD_CAB_APPROVAL=docs/squad/features/mcp-data-platform/8-gate2/cab-approval.md scripts/squad/deploy.sh prod`
   — refuses unless the **CHG-003** `cab-approval.md` has `status: approved`; CEO confirms the prompt.
   This deploys the code; it does **not** open live egress on its own.
4. The CEO runs the operator runbook in §11 to actually turn it on: set the read-only Atlassian token
   + `MCP_EGRESS_ALLOWLIST='*.atlassian.net'` + `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, then
   `doctor → ingest → status → verify`. This is where the **first real egress** happens, under the
   CEO's hand.
5. `scripts/squad/smoke.sh prod` — `doctor` on the live-locally servers plus the ingest path smoke
   (egress default-deny proven, read-only surface, no write tool).
6. (Optional, separable) the one-time `huggingface.co` model download to begin measuring NFR-003 — a
   distinct operator step, can be deferred without blocking the Confluence pull.

Duration: ~15–30 min (deploy + first pull + smoke); the CEO drives the real pull. DK3 applies — the
per-env Redis ACL must never be the shared/dev `mcp_ro` grant (see §9.1).

## 6. Rollback plan

- **Triggers:** smoke fails on prod (`doctor` red, a write tool appears on the surface, or egress stops
  being default-deny — an unlisted host is reached); error rate above baseline × 2 for 5 min; a P1
  journey (Confluence pull → `kb_semantic_search`, or a base read) fails a canary check; the stored
  token leaks into a log or result. Any trigger → roll back immediately, no approval needed.
- **Steps (`scripts/squad/rollback.sh prod`), effect in `< 60 s`:**
  1. **Unset `MCP_EGRESS_ALLOWLIST`** → the default-deny egress guard refuses every outbound host; the
     real-ingestion pull stops reaching Atlassian at once.
  2. **Remove the stored Atlassian token** (delete the token file / unset `MCP_CONFLUENCE_API_TOKEN*`)
     → no credential remains on the machine.
  3. **Stop the ingest connector** (stop the scheduler / do not launch `mcp-ingest run`) → no further
     pull.
  Then verify with `scripts/squad/smoke.sh prod` and `scripts/squad/notify.sh rollback "<trigger>"`.
- **The live egress is itself gated off by default.** Real egress only happens while
  `MCP_INGEST_ALLOW_LIVE_EGRESS=true` is set; it **defaults off**, so the fastest rollback is simply
  not setting (or unsetting) that flag — the system reverts to the stubbed, no-egress behaviour it
  shipped with.
- **Measured time-to-rollback:** **< 60 seconds** — unsetting the allow-list and the live-egress flag
  takes effect on the next request; removing the token and stopping the connector are single steps. A
  shared-environment rehearsal is **not applicable** (no shared PRE/PROD service); the figure is the
  real cost of the documented steps, same pattern proven at the first go-live
  (`7-release/release-log.md`).
- **Data rollback:** CHG-003 adds **no schema**, so there is no down-migration. If real-ingested
  content must be removed, `mcp-ingest prune` / truncate the ingested rows for the affected source; the
  base schema and the first go-live's demo data are untouched. Upstream sources (incl. Confluence /
  Jira) are read-only — nothing to reverse upstream.

## 7. Post-go-live verification

- **Smoke:** `scripts/squad/smoke.sh prod` — `doctor` for the live-locally servers + the ingest-path
  checks (egress default-deny proven on an unlisted host, read-only surface intact, no write tool,
  token absent from output). The five remote-only sources stay a manual Gate-C checklist (VPN/creds).
- **Observation window:** **30 minutes** (per `squad-observability`); SLIs read at start (baseline)
  then every 5 minutes. For a per-user stdio server with no network listener the SLI is **liveness**
  (process up + startup read-only/credential gate green) plus the egress invariant (an unlisted host
  stays refused) and the token-never-leaks invariant.
- **SLI read-out command:** error rate = `count(status=error)/count(all)` by `tool`, p95 =
  `p95(duration_ms)`, computed from the JSON stderr logs in `MCP_LOG_FILE`
  (`4-design/architecture.md` → Observability); plus `egress_denied` (count of refused outbound hosts,
  which should be 0 for configured hosts and non-zero only if something tries an unlisted host). Each
  rollback trigger maps to one SLI (doctor-fail → readiness; error-rate → status=error ratio; p95 →
  latency; egress-breach → `egress_denied` on a host that should be refused; token-leak → the
  scrub-guard test on the live log sample).
- **Success criteria:** smoke passes and no rollback trigger fires during the window.

## 8. Communication

- **Before:** CEO (Gate 2 approval) notified via chat (`NOTIFY_CHANNELS=chat`), with the §1 summary and
  the §11 operator runbook, including the warning that the production (local) step and the first real
  egress need the CEO's confirmation and a real read-only Atlassian token at run time.
- **After:** `scripts/squad/notify.sh deployed "<version>"` on success;
  `scripts/squad/notify.sh rollback "<trigger>"` on any rollback. Channel: chat.

## 9. Residual risks

### 9.0 Residual risks the CEO must explicitly accept at Gate 2 (carried, not hidden)

1. **NFR-003 semantic recall is STILL UNVERIFIED.** Egress now *can* be opened, but no real golden-set
   has been measured — both the CI arm and the dev arm use the `DeterministicFakeProvider`, so the
   ingest → store → verify and read-only paths are proven, retrieval **quality** is not. The real
   measurement needs the real `bge-m3` download (TC-131, `@live`) + the spike-S2 bake-off on a real
   company golden-set. This is the same risk the CEO accepted at the first go-live (D-002 DK1), carried
   forward; opening egress makes it *measurable*, not *proven* (L-002). **Needs explicit PO/CEO risk-
   acceptance** that v1 ships with recall unproven. Mitigation already in place: `calibration_status=
   uncalibrated`, no number invented.
2. **Confidence thresholds `τ_fact` / `τ_low` are UNCALIBRATED** (`calibration_status=uncalibrated`).
   The FACT↔LOW_CONFIDENCE boundary is a `# THRESHOLD TBD` constant, meaningful only on a measured
   golden-set (NFR-010, same egress blocker as item 1). The τ-independent honesty guarantees
   (no-evidence ⇒ UNKNOWN, CONFLICT exposed, no fabrication) hold regardless. **Accept:** the FACT/LOW
   split is advisory until calibrated. (This matters only when the postponed CHG-001 grounding surface
   is turned on; it is carried here for completeness.)
3. **R-C3-001 (MEDIUM): `HttpEmbeddingProvider` is not egress-gated.** `embedding/http.py` builds a raw
   `httpx` client and POSTs to a configured URL **without** passing through the single `check_egress`
   choke point. It is **off by default** (the approved path is `provider=local` + the one-time HF
   download, which *is* gated), pre-existing (not introduced by CHG-003), and the runbook never
   instructs its use, so it falsifies no AC/NFR. **Accept as residual:** do not use `provider=http`
   until it is routed through the egress guard; **harden before any `provider=http` use.**
4. **E-mcp-data-platform-008 (S3, open/accepted): base test-plan gap.** The Must base FR-005 (Kibana
   saved-objects / dashboard link) has only integration-level TCs (TC-019/TC-020), no dedicated E2E TC.
   Pre-existing (same in HEAD before CHG-003), non-blocking (Kibana is covered by integration + the
   9-server stdio E2E TC-070), CTO-deferred. **Declared as an accepted-open S3 residual** — fixing it
   needs a base TC renumber that protocol §4 forbids mid-change.
5. **Real-tenant pull + Claude Desktop registration (NFR-005) are manual operator Gate-C steps.** The
   real Confluence pull against `tnexwm.atlassian.net` (TC-121, `@live`), the real `bge-m3` download
   (TC-131, `@live`), and registering the servers in Claude Desktop all need the CEO's real read-only
   Atlassian token + network, none exercised in `dev`. They are the §11 operator checklist, gated
   behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`.

### 9.1 Accepted LOW / operational residuals (CTO-accepted, no open S3/S4 defects beyond §9.2)

- **R-C3-002 (LOW):** ADR-0023 §6a prose reads broader than the enforcement (it does not name the
  `provider=http` path as an out-of-scope exception). Documentation debt that feeds R-C3-001; no
  runtime impact.
- **CI is not runner-enforced.** The five gates run via `make ci` on demand; no runner/hook blocks a
  merge that skips them. Gate-C sign-off is on manually-run gates. (Base R-016, carried.)
- **157 Debian-initdb + ~30 passwordless-DSN/@live Postgres tests skip on this macOS host.**
  `packages/conftest.py::_find_pg_bin()` probes only the Debian `initdb` path; the same behaviours are
  covered end-to-end against real pgvector by the Confluence-Cloud e2e + TC-124 (re-run green here with
  the container + `PGPASSWORD`). Portable `_find_pg_bin()` is a carried backend follow-up (D-003).
- **DK3 — Redis dev ACL per-env, never shared.** The dev compose `mcp_ro` ACL grants broad reads +
  `default nopass +@all`; not reachable via any tool (every command is gated), but it must never be
  reused for a staging/shared Redis. Recorded in `infra/env-promotion-chg001.md`.

### 9.2 Error ledger (S3/S4 open, accepted by the CTO)

- **E-mcp-data-platform-008** (S3, test) — **open, accepted-open residual.** Base Must FR-005 (Kibana)
  has no dedicated E2E TC; pre-existing, non-blocking, CTO-deferred (see §9.0 item 4). This is the only
  open ledger entry.
- All other entries are closed/verified: **E-mcp-data-platform-009** (S2 security, token-scrub
  production wiring) verified-closed this slice; E-001…E-007 closed/verified from the earlier scopes.
  `errors.sh list --feature mcp-data-platform` reports **0 open S1/S2**.

## 9a. Baseline check

- **Scope:** as approved at CHG-003 Gate 1 (`2-gate1/plan-approval.md` CHG-003 section, Option B;
  deviation ~86/100 accepted; ADR-0023). Delivered FR-023…FR-027 in full; the deferrals in §9.0 are
  quality/measurement/operator items, not scope cuts. **Delta vs plan: 0% scope reduction.** CHG-001
  Company Knowledge is **postponed** by CEO choice, not cut from CHG-003 (it was never CHG-003 scope).
- **Cost / schedule:** CHG-003 is lean (D-006) — critical path ~19 agent-h, `schedule_delta −20.8%`
  vs the D-006 24-h baseline; run cost **≈ $0/month delta** (existing Atlassian tenant, no new paid
  tier; HF download is one-time bandwidth). The two accepted baseline relaxations (no-egress →
  configured-source-host egress; vendors=none → +Atlassian) are exactly what the CEO approved at
  Gate 1; the platform baseline will be amended after go-live (vendors += Atlassian; egress allow-list
  += `*.atlassian.net`, `huggingface.co`).
- **CTO decisions that changed the baseline:** D-006 (sizing / escalate to Gate 1, lean track). No
  decision moved cost/schedule past `ESCALATE_SCHEDULE_PCT=20` / `ESCALATE_COST_PCT=15`; the only
  threshold crossing is the deviation itself, which is why it went to CEO Gate 1 and was approved.

## 10. CEO decision

Filled by the orchestrator in `docs/squad/features/mcp-data-platform/8-gate2/cab-approval.md` (a **new**
CHG-003 approval; no prior approval covers this change), not here.

## 11. Operator runbook — how the CEO turns it on

This is the sequence the CEO runs after approving Gate 2 and after `deploy.sh prod` has placed the
code. It is the real-egress step: it needs a real read-only Atlassian token and network. The runbook
describes how to make the token; it never contains one. Full version: `4-design/architecture.md`
Appendix A.

**Step 1 — make a read-only Atlassian API token (in the Atlassian UI, not here).**
Sign in as a **viewer-only** account (read access to the spaces you want, no edit/admin — `doctor` will
reject a write-capable account on purpose). At **id.atlassian.com → Security → Create and manage API
tokens → Create API token**, label it e.g. `mcp-ingest-readonly`, and copy it once (Atlassian shows it
once). Note the account **email** and the base URL `https://tnexwm.atlassian.net`.

**Step 2 — set the env (token via a file, never inline, out of git).**

```bash
# token in a file, mode 600, outside the repo
printf '%s' 'PASTE_READONLY_TOKEN_HERE' > ~/.secrets/atlassian_token && chmod 600 ~/.secrets/atlassian_token

export MCP_CONFLUENCE_BASE_URL="https://tnexwm.atlassian.net"
export MCP_CONFLUENCE_EMAIL="viewer-account@your-domain"
export MCP_CONFLUENCE_API_TOKEN_FILE="$HOME/.secrets/atlassian_token"   # never MCP_CONFLUENCE_API_TOKEN=... inline
export MCP_CONFLUENCE_FLAVOR="cloud"

# open egress ONLY to Atlassian, and arm the live-egress gate (default off) for this run
export MCP_EGRESS_ALLOWLIST='*.atlassian.net'
export MCP_INGEST_ALLOW_LIVE_EGRESS=true
```

**Step 3 — doctor (health + read-only proof).**

```bash
uv run mcp-confluence doctor
# healthy:
#   config: ok (base_url=https://tnexwm.atlassian.net, flavor=cloud)
#   credentials + read-only check: ok
```

If the account can write, the last line is `FAILED` and names the permitted write operations — fix the
token (use a viewer-only account); do **not** set `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` for the real
token.

**Step 4 — ingest (dry-run first, then real).**

```bash
uv run mcp-ingest run --source confluence --limit 5 --dry-run   # crawl + chunk + hash, no DB write
uv run mcp-ingest run --source confluence                       # real incremental pull
# exit 0 = success, 1 = partial, 2 = failed, 3 = another run holds the lock
```

**Step 5 — status (freshness).**

```bash
uv run mcp-ingest status --json
# expect confluence with a recent last_success_at, document/chunk counts > 0, small staleness_hours
```

**Step 6 — verify through semantic search.** Register `mcp-pgvector` in Claude Desktop
(`mcp-common config-emit --server pgvector`), then ask a question whose answer is in a page you just
ingested. A healthy result is `status: ok` with items whose `source_uri` points back at
`tnexwm.atlassian.net` and a non-empty citation. No match ⇒ `status: empty` with the best-similarity
warning — the honest "not found", not a fabricated answer.

**The other three ingestable sources** (GitLab, Jira, OpenSearch) follow the same four steps with their
own `MCP_<SOURCE>_*` env; OpenSearch is off by default and ingests only indices allow-listed in
`MCP_INGEST_OPENSEARCH_INDICES`. **The five live-only sources** (CloudWatch, Kibana, Kafka, Redis,
SQS/SNS) are never ingested — `mcp-<src> doctor` → register in `claude_desktop_config.json` →
`tools/list` smoke; "integrated" = reachable + read-only + registered, 0 write tools.

> **One-time model download (separable, optional).** To begin measuring NFR-003, open `huggingface.co`
> for the single `bge-m3` download step only; `HF_HUB_OFFLINE` flips online for the download then back
> to offline for serving. Until then ingestion embeds with the fake provider — the content is real, the
> semantic *quality* is not yet proven (L-002). Do not read any recall number as quality proof until the
> bake-off runs on the real model.

> **Rollback at any point (< 60 s):** unset `MCP_EGRESS_ALLOWLIST` (every outbound host is refused),
> remove `~/.secrets/atlassian_token` / unset `MCP_CONFLUENCE_API_TOKEN*`, stop the ingest connector.
> Live egress is off whenever `MCP_INGEST_ALLOW_LIVE_EGRESS` is unset — the system reverts to its
> stubbed, no-egress behaviour.

---

# Appendix A — superseded pack: CHG-001 Company Knowledge (POSTPONED, kept verbatim)

> Kept verbatim for the rollback-pattern reference and the baseline record. **CHG-001 is POSTPONED** —
> the CEO deferred it at its own Gate 2 (no `cab-approval.md` written). Its code is built and reviewed
> and stays on disk, but it is **not** part of the CHG-003 go-live above. This pack is NOT the active
> change.

| Field | Value |
|---|---|
| Change ID | CHG-mcp-data-platform-20261002 (CHG-001 Company Knowledge) — **POSTPONED** |
| Version | CHG-001 slice on base `b898040`; tag `release/mcp-data-platform-chg001-20261002` (not cut — postponed) |
| Type | normal |
| Risk rating | medium (medium impact × low likelihood) |
| Status | **POSTPONED by the CEO at Gate 2 (2026-10-02T10:00); no cab-approval.md written** |

- **What it would change:** the Company Knowledge layer on top of the live 9 sources — 13 read-only
  tools (8 Knowledge MCP + 5 Live Jira), a hybrid-RAG engine (tsvector + pgvector + RRF, local offline
  reranker), a server-side permission choke point #1, a grounding gate #2, an in-process gateway
  boundary, Jira as source #10, knowledge domains (migrations 0007/0007b/0008). 0 write tools; tool
  surface 62. Delivered FR-016…FR-022 (26 AC); review CHG-001 round 2 APPROVE (0 critical/high/
  medium/low); E-001…E-007 closed/verified.
- **Rollback pattern (reused by CHG-003 above):** remove the per-user stdio server entries + stop the
  Jira ingest connector → effect immediate, **< 60 s**; expand-only migrations; truncate the new `kb`
  knowledge tables if the data must go.
- **Why postponed:** the CEO chose to make the system real first (CHG-003: real ingestion + egress,
  Confluence first) before turning on the Company Knowledge layer. CHG-001 artifacts are preserved;
  its own Gate 2 can be revisited later.
- **Residual risks it carried (now folded into the CHG-003 §9.0 where still relevant):** NFR-003
  recall UNVERIFIED (DK1), τ uncalibrated, CI not runner-enforced, real-source/Claude-Desktop manual
  Gate-C, Redis dev ACL never shared (DK3).

---

# Appendix B — superseded pack: 9-source read-only go-live (2026-10-01)

> Kept verbatim for the rollback-pattern reference and the baseline go-live record. This scope is
> already live locally (`7-release/release-log.md`, `8-gate2/cab-approval.md` dated 2026-10-01).

| Field | Value |
|---|---|
| Change ID | CHG-mcp-data-platform-20261001 |
| Version | `0363fcc` (branch `claude/zealous-johnson-yb3t2q`; tag `release/mcp-data-platform-20261001`) |
| Type | normal |
| Risk rating | medium (medium impact × low likelihood) |
| Window | 2026-10-02, 10:00–11:00 +07:00 (used 2026-10-01 local go-live); observation 30 min |
| CTO recommendation | READY_FOR_CAB — decisions.md D-002 |

- **What changed:** 9 read-only MCP servers (Confluence, GitLab, OpenSearch, Kibana, CloudWatch,
  Kafka, Redis, SQS/SNS, Postgres+pgvector) + ingest/embedding pipeline, per-user local stdio, 49
  read-only tools, migrations 0001–0006 on empty `kb`. Delivered FR-001…FR-015.
- **Evidence:** dev regression 1846 passed / 174 skipped / 0 failed; e2e 34/34 on real pgvector; review
  round 2 APPROVE (0 critical/high); E-001…E-004 closed/verified; `verify_tool_surface` 42/42.
- **Rollback pattern (reused by CHG-003 above):** remove the per-user stdio server entry + stop the
  ingest scheduler → effect immediate, **< 60 s**; expand-only migrations; `docker compose … down -v`
  for the local infra.
- **Accepted at Gate 2 (2026-10-01):** NFR-003 UNVERIFIED (DK1), migration-locking to be fixed before
  CHG-001 E1 (DK2 — done in CHG-001 T-087), Redis dev ACL never shared (DK3), CI-doc-only / live-source
  manual follow-ups. Go-live recorded in `7-release/release-log.md`.
