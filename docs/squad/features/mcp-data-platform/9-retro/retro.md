# mcp-data-platform — Retrospective (2026-10-01)

> Blameless retro after a successful **local** go-live (demo pass, smoke pass) and before CHG-001
> (Company Knowledge layer) opens. Facts only, from `state.json` history, `records/errors.md`
> (E-001..E-004), `records/decisions.md` (D-001/D-002), `6-verify/review-report.md` (R-001..R-027),
> `7-release/release-log.md`. Track: large (full retro). "Process, not role" throughout.

## Outcome
- **Delivered.** 9 read-only MCP servers (Confluence, GitLab, OpenSearch, Kibana, CloudWatch, Kafka,
  Redis, SQS/SNS, Postgres+pgvector) + the ingest/embedding pipeline, local per-user stdio.
- Go-live = the CEO's local macOS host (per-user stdio; no shared hosted service). Deployed
  2026-10-01, Gate 2 approved same day; **vs baseline:** on the Gate-A/B option, 0% scope reduction,
  cost/schedule inside the Gate-1 envelope (`cab-pack.md` §9a, D-002).
- Shipped with **one explicit, CEO-accepted residual risk**: NFR-003 (semantic retrieval quality)
  **UNVERIFIED** — accepted at Gate 2 (DK1), to be measured during CHG-001 when HF egress + ADR-0010
  model are resolved.

## Flow metrics
- **Lead time:** intake 2026-10-01T00:00 → deploy 2026-10-01T21:18 (one compressed delivery, with
  two user-initiated stop/resume boundaries across local↔cloud sessions).
- **Phases:** intake → po (Gate A) → ba → sa (3 runs) → lead (Gate B) → qa-plan → backend
  (Setup+Phase1+Phase2+Phase3a+Phase3b, batched) → sa reconcile → lead/qa-plan stale re-runs →
  qa-verify → review (round 1 → fixes → round 2 Gate C) → release (cab-pack) → Gate 2 → deploy → watch.
- **Loops used:** `review=1` (both fix passes stayed inside round 1), `spec=1` (SA contract
  reconciliation mid-build), `qa=0`.
- **RETURNs / re-runs:** 1 SA reconciliation (13 contract_issue items) with downstream lead +
  qa-plan stale re-runs; 1 review fix cycle (R-001..R-005 in parallel, disjoint files).
- **Escalations:** 1 — CHG-001 sized LARGE (deviation 100/100), ESCALATE to CEO Gate 1 (D-001);
  CEO chose Option A (close old scope first). CHG-001 not started.
- **Interruptions:** 3 user-initiated stops (backend Setup batch killed mid-agent; qa-verify run 1
  stopped — out of cloud credit; qa-verify run 1 stopped again mid-way), each resumed audit-first.
- **One SA crash** on a provider rate-limit during the 1st SA run (artifacts already on disk;
  recovered by a reconciliation pass, not a redo).

## Token use
- **No per-stage token metrics are available for this feature:** `TOKEN_BUDGET_M=0` in
  `.kiro/squad/config.env` and no `ecc:cost-tracking` metrics log exists, so `state.json.history`
  carries no token figures. A token table cannot be reconstructed honestly and is therefore omitted
  rather than fabricated.
- **Qualitative cost signal** (where effort visibly concentrated, from history): the SA stage
  (3 runs: crash-recover + staleness reconcile + 13-item contract reconcile) and the backend stage
  (5 batches + 2 resume-audits after interruptions) were the most expensive by dispatch count; the
  review fix cycle was cheap (disjoint parallel fixes, stayed in round 1). The process fix for the
  SA cost is the staleness/contract-SSOT discipline already captured below, not more tokens.
- Follow-up: set a real `TOKEN_BUDGET_M` and wire `ecc:cost-tracking` before CHG-001 so the next
  retro can report spend per stage.

## Quality metrics
- **Defects by environment:** dev/review = 5 HIGH + 12 MEDIUM + 10 LOW (round 1); escaped to a live
  env = **0** (no rollback, no incident). UAT/PRE = n/a by design (per-user stdio, no shared service;
  PRE-equivalent ran on a real dev host + Docker, D-002).
- **Review findings by severity (round 1):** 0 CRITICAL, 5 HIGH (R-001..R-005), 12 MEDIUM, 10 LOW.
  Round 2: **APPROVE**, 0 CRITICAL / 0 open HIGH; 22 MEDIUM/LOW deferred as v1 residual risk.
- **S1/S2 error ledger:** 4 opened (E-001/002/003 S2 security, E-004 S1 design), **4/4 verified &
  closed**, 0 open, 0 accepted-open. `errors.sh open` clean before Gate 2.
- **Rollbacks:** 0. **Flaky/harness bugs:** TC-069 (stdio e2e harness closed stdin then called
  `communicate()` → `ValueError`) — a test-harness bug, not a product defect; the fix was lost across
  a stop/resume and had to be re-applied before qa-verify finished.
- **Tests at go-live:** dev regression 1846 passed / 174 skipped / 0 failed; full `e2e/` 34/34, 0
  skipped on real PostgreSQL 16.15 + pgvector 0.8.6; 10 P1 pgvector E2E 10/10; BE coverage 89.97% on
  changed code; read-only tool surface 42/42.

## Defects
From `errors.sh list --feature mcp-data-platform` (all S1/S2; one line each):

- **E-mcp-data-platform-001** (E-001) · S2 security — introduced in **backend**, escaped **backend → qa-plan → qa-verify**.
  GitLab `get_text()`/`get_job_trace()` buffered an unbounded CI trace into RAM (OOM-able);
  `response_too_large` was declared in the contract/enum but never raised or tested. Root cause: a
  thin `.text` accessor written for small bodies in Phase 1 was reused for job traces later without
  revisiting the download path, and the model-output budget was conflated with a download bound.
  **Prevention in place:** streaming + `Content-Length`/cumulative-byte cap raising
  `RESPONSE_TOO_LARGE`, with 3 regression tests incl. the lying/absent-header fallback. ✅
- **E-mcp-data-platform-002** (E-002) · S2 security — introduced in **backend**, escaped **backend → sa (ADR-0015 A1) →
  qa-plan → qa-verify**. `DEFAULT_PATH_DENY` matched only suffix-shaped names, so `.env.local`,
  `.env.production`, `id_ed25519`, `*.key`, `*.p12`, `.npmrc`, `.netrc`, `*.tfstate` were ALLOWED —
  and `mcp_ingest` shares the list, so a slipped secret would persist into `kb.chunks`. Root cause:
  an illustrative deny-list treated as complete; escaped stages reasoned about the globs present, not
  the secret filenames absent; no test asserted specific real secret filenames are denied.
  **Prevention in place:** broadened deny-globs matched on full path + basename, with inheritance
  tests on both the GitLab and ingest sides. ✅
- **E-mcp-data-platform-003** (E-003) · S2 security — introduced in **backend**, escaped **backend → qa-plan → qa-verify**.
  The error/log path bypassed `scrub()` (`to_error_envelope` + `JSONStderrFormatter` serialized raw
  `str(exc)`), contradicting `redact.py`'s "scrub everything leaving the process" guarantee, reachable
  from 6 packages. Root cause: redaction was added at the success boundary and the ingest pipeline,
  but the error-envelope and log-formatter paths were built separately and never routed through the
  same structural choke point; the guarantee lived in a docstring, not in code, and no test fed a
  secret-shaped string down the error/log path. **Prevention in place:** both paths now route through
  `scrub()`/`_scrub_recursive` at the single build point, with end-to-end regression tests. ✅
- **E-mcp-data-platform-004** (E-004) · S1 design — introduced in **sa (ADR-0011 A3 wording) + qa (signoff/regression wording)**,
  escaped **sa → qa-plan → qa-verify → release**. "recall ≥ 0.95" was presented as NFR-003 evidence
  but measures only ANN-index correctness (synthetic corpus+queries from one seed under a fake
  provider → ~1.0 even if the embedding is noise). Root cause: two distinct meanings of "recall"
  (index-correctness vs semantic-relevance) were never separated, and the real (blocked) NFR-003
  measurement was not flagged UNVERIFIED next to the number. **Prevention in place:** ADR-0011 A3 +
  architecture.md NFR-003 cell + signoff relabelled explicitly; NFR-003 carried as a Gate-C caveat and
  CEO-accepted residual risk, not proven. ✅

**RECURRING pattern (`errors.sh summary`):** 3 of 4 defects (E-001/002/003) are all **security**, all
**introduced in backend**, and all **escaped qa-plan + qa-verify**. The common root cause is one
class: *a safety guarantee asserted in prose (docstring/ADR) but not enforced at a single structural
choke point, with no test feeding the adversarial input*. The stage that keeps letting this class
escape is **qa-plan/qa-verify** (no adversarial/negative test for the declared guarantee) with the
origin in **backend** (guarantee not centralised). This is the feature's first error ledger, so no
cross-feature recurrence yet — but the pattern is strong enough to seed a lesson now (L-001).

## What worked
- **Read-only invariant (NFR-001) held end to end.** Reviewer found no reachable mutate path across
  all 9 servers (layered allowlists, parameterised SQL only, no SSRF, no `verify=False`); the go-live
  demo proved it live (`kb_delete_document` → "Unknown tool"). Evidence: review-report §3; release-log.
- **The review caught every HIGH before any live env.** 0 defects escaped to prod; round 1 found all
  5 HIGH, the parallel disjoint-file fix closed them inside one loop, round 2 verified on disk + re-run.
  Evidence: R-001..R-005, state history `rerun_done`/`approved`.
- **Resilient stop/resume + audit-first.** Three user interruptions and an SA rate-limit crash did
  not corrupt state; each resume audited disk before writing. Evidence: history
  `stopped_by_user`/`resumed` entries; backend "audit-first" resume note.

## What hurt
- **Guarantees in prose, not at a choke point (E-001/002/003).** 5-whys: HIGH security findings →
  because the guarantee (size cap / deny-list / scrub) was documented but not centralised → because
  each path was added incrementally and reused without revisiting the invariant → because no
  adversarial/negative test exercised the declared guarantee → because qa-plan tested the happy path
  of the feature, not the stated safety promise. Fix: centralise the guarantee + add an adversarial
  test for it (L-001). Blameless: a process gap between "documented invariant" and "enforced
  invariant", not a person.
- **"recall" meant two things (E-004).** 5-whys: a Gate-C-level S1 → because an ANN-correctness number
  was read as semantic-quality proof → because one word named two measurements → because the real
  NFR-003 measurement was blocked (HF egress 403 + ADR-0010 unselected) and the blockage was not
  surfaced next to the number → because the environment constraint (egress/VPN) was discovered late.
  Fix: name the two recalls distinctly and flag blocked NFRs UNVERIFIED (L-002); check egress early
  (follow-up to CHG-001). Blameless: ambiguous terminology + a late-surfaced environment block.
- **Layout migration broke CI by moving docs under code's feet.** `migrate-layout.sh --force`
  `git mv`'d 11 files; code/tooling pointing at the old `api-contract.yaml` path broke CI, fixed only
  by a layout-tolerant `find_contract_path`. 5-whys: CI red → path moved → resolver hard-coded the old
  path → no path-tolerant resolution for relocatable artifacts. Fix: path-tolerant resolution (L-003).
- Two environment blocks shaped the whole quality story and must be cleared **before** CHG-001, not
  during it: **(a)** HF egress 403 blocked the real embedding bake-off (ADR-0010) and the only real
  NFR-003 measurement; **(b)** `packages/conftest.py::_find_pg_bin()` is Debian-only, so 153
  package-level pg tests skip on the CEO's macOS host (behaviour is covered via e2e `MCP_E2E_PG_URL`,
  but the package fixture is not portable). Carried as follow-ups below, not lessons (environment/
  single-fix, not generalisable rules).

## Lessons
Added to `docs/squad/knowledge/lessons.md` (max 3 per retro; the fixture-portability and egress items
are carried as CHG-001 follow-ups, not lessons, being single-fix/environment rather than general rules):
- **L-001** · all — centralise a declared safety guarantee at one choke point and add an adversarial
  test for it. [E-001, E-002, E-003, R-001, R-002, R-003]
- **L-002** · squad-sa — never let one metric name two measurements; flag a blocked NFR UNVERIFIED
  next to any proxy number. [E-004, R-004, D-002]
- **L-003** · all — make references to relocatable artifacts path-tolerant so a layout `git mv`
  cannot break CI. [state 2026-10-01T16:43 layout_migrated, 2026-10-01T17:20 rerun_done]
