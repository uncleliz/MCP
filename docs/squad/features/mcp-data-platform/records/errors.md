# mcp-data-platform — Error ledger

## E-mcp-data-platform-001 · S2 · 2026-10-01
- Category: security
- Found: review (round 1) · by: reviewer (security-reviewer, Reviewer) — finding R-001
- Introduced: backend
- Escaped: backend, qa-plan, qa-verify
- Symptom: `GitLabClient.get_text()` returned `response.text` with no `stream=True`, no `Content-Length` check and no byte ceiling; `get_job_trace()` buffered a whole CI job trace (hundreds of MB possible) into RAM before `_tail_bytes()` truncated it, so a single `gitlab_get_pipeline(include_failed_job_trace=true)` call could OOM-kill the `mcp_gitlab` process. `ErrorCode.response_too_large` was declared in both the contract and the enum but never raised and never tested.
- Evidence: R-001; `packages/mcp_gitlab/src/mcp_gitlab/client.py:205-211`, `read_api.py:794-839`
- Owner of fix: backend
- Recurrence of: none

### E-mcp-data-platform-001 · fix · 2026-10-01
- By: backend
- Root cause: `get_text` was written as a thin `.text` accessor during Phase 1 when only small text bodies (file contents) were in scope; job-trace reuse arrived later without revisiting the download path. The tool-output budget (`MCP_MAX_OUTPUT_BYTES`) only bounds what is *returned to the model*, so reviewers and the plan conflated it with a download bound — there was no test asserting behaviour on an oversized body, so the gap was invisible.
- Fix: `packages/mcp_gitlab/src/mcp_gitlab/client.py` — `get_text` now streams via `http.stream("GET", ...)`, rejects on declared `Content-Length > cap` and on cumulative bytes read `> cap`, raising `ErrorCode.RESPONSE_TOO_LARGE`; `get_job_trace` passes the new `max_job_trace_bytes` cap. New setting `MCP_GITLAB_MAX_JOB_TRACE_BYTES` (default 10 MiB) in `settings.py`, documented in `.env.example`.
- Prevention: regression tests in `packages/mcp_gitlab/tests/test_client.py` — `test_get_job_trace_over_cap_raises_response_too_large`, `test_get_job_trace_cap_enforced_even_if_content_length_lies` (byte-counting fallback when the header lies/absent), `test_get_job_trace_passes_through_when_within_cap`.

## E-mcp-data-platform-002 · S2 · 2026-10-01
- Category: security
- Found: review (round 1) · by: reviewer (security-reviewer, Reviewer) — finding R-002
- Introduced: backend
- Escaped: backend, sa (ADR-0015 A1), qa-plan, qa-verify
- Symptom: `DEFAULT_PATH_DENY = "*.env,*secret*,*credential*,*.pem,id_rsa*"` only matched names *ending* in a sensitive suffix (fnmatch, no leading `*`). Verified with real `fnmatch`: `.env.local`, `.env.production`, `config/.env.staging`, `id_ed25519`, `certs/server.key`, `sa.p12`, `truststore.jks`, `.npmrc`, `.netrc`, `terraform.tfstate` were all ALLOWED. Because `mcp_ingest` imports the same list, a slipped secret would also be persisted into `kb.chunks`.
- Evidence: R-002; `packages/mcp_gitlab/src/mcp_gitlab/settings.py:14`, `packages/mcp_ingest/src/mcp_ingest/pipeline/redact.py:17`
- Owner of fix: backend
- Recurrence of: none

### E-mcp-data-platform-002 · fix · 2026-10-01
- By: backend
- Root cause: the deny-glob was authored as a short illustrative list and treated as complete; the escaped stages reasoned about the globs that *were* present rather than enumerating the secret filenames that are *not* `*.env`/`*secret*`-shaped (dotfiles, key material, IaC state). No test asserted that specific real-world secret filenames are denied, so the narrowness was never exercised.
- Fix: broadened `DEFAULT_PATH_DENY` in `packages/mcp_gitlab/src/mcp_gitlab/settings.py` to also cover `*.env.*`/`.env`, `id_dsa*`/`id_ecdsa*`/`id_ed25519*`, `*.key`/`*.pfx`/`*.p12`/`*.jks`, `.npmrc`/`.netrc`, `*.tfstate`/`*.tfstate.*`/`*.tfvars`; matched against both full lowercased path and basename (behaviour unchanged, list widened). `mcp_ingest` inherits it via its existing `from mcp_gitlab.settings import DEFAULT_PATH_DENY`. Documented in `.env.example`.
- Prevention: regression tests — `packages/mcp_gitlab/tests/test_client.py::test_R_002_deny_glob_blocks_previously_missed_secret_filenames` (14 filenames) and `packages/mcp_ingest/tests/test_stage_redact.py::test_R_002_adr_default_catches_previously_missed_secret_filenames` (proves the ingest side inherits the broadened default verbatim).

## E-mcp-data-platform-003 · S2 · 2026-10-01
- Category: security
- Found: review (round 1) · by: reviewer (python-reviewer, security-reviewer, Reviewer) — finding R-003
- Introduced: backend
- Escaped: backend, qa-plan, qa-verify
- Symptom: the error path bypassed `scrub()`, contradicting `redact.py`'s documented guarantee that scrub applies to "(a) every log record and (b) every free-text before returning in a tool result". `to_error_envelope()` serialized `error.message`/`error.details` verbatim; three `_NON_HTTPX_SDK_RULES` interpolate raw `str(exc)` (e.g. `f"Postgres operational error: {exc}"`), and `JSONStderrFormatter.format()` did not scrub either — so a `psycopg.OperationalError` carrying a DSN/credential could reach the client/log unredacted, reachable from 6 packages.
- Evidence: R-003; `packages/mcp_common/src/mcp_common/errors.py:95-108,198-248`, `logging.py:53-72`
- Owner of fix: backend
- Recurrence of: none

### E-mcp-data-platform-003 · fix · 2026-10-01
- By: backend
- Root cause: redaction was added at the success boundary (`safe_text`/`sanitize_json`) and in the ingest pipeline, but the error-envelope and log-formatter paths were built separately and never routed through the same choke point; the architecture's "scrub everything leaving the process" guarantee was asserted in a docstring but not enforced structurally. No test fed a secret-shaped string down the error/log path, so the bypass was latent.
- Fix: `packages/mcp_common/src/mcp_common/errors.py` — `to_error_envelope()` now scrubs `message` (`scrub(...)[0]`) and recursively scrubs every string leaf of `details` (`_scrub_recursive`), the single point every `ErrorEnvelope` is built through. `packages/mcp_common/src/mcp_common/logging.py` — `JSONStderrFormatter.format()` scrubs both the message and the formatted `exc_info`, the single formatter every server's handler uses.
- Prevention: regression tests — `test_errors.py::test_R_003_to_error_envelope_scrubs_secret_shaped_message` / `..._details` / `..._does_not_mangle_ordinary_messages`; `test_tooling.py::test_R_003_register_tool_error_envelope_scrubs_secret_in_message` (end-to-end through `register_tool`, asserts both rendered text and `structuredContent`); `test_logging_stdout_guard.py::test_R_003_log_message_with_secret_shaped_string_is_scrubbed`.

<!-- Note: R-004 (signoff recall claim) is a documentation-claim correction, owner sa+qa.
     Backend only edited the claim wording in docs/signoff/phase-3.md (allowed under the
     mcp-data-platform layout override); the ADR-0011 edit a prior session had left on disk
     was reverted here because docs/adr/ is SA-owned. Not opened as a backend E-id. -->

## E-mcp-data-platform-004 · S1 · 2026-10-01
- Category: design
- Found: review (round 1) · by: reviewer (rag-pipeline-reviewer CRITICAL, database-reviewer, Reviewer) — finding R-004
- Introduced: sa (ADR-0011 A3 wording) + qa (signoff/regression wording)
- Escaped: sa, qa-plan, qa-verify, release
- Symptom: the "recall ≥ 0.95" number was presented as evidence of retrieval quality / NFR-003, but it measures only ANN-index correctness (HNSW returns the same rows as brute-force with `enable_indexscan=off`). `recall_benchmark.py` draws corpus *and* queries from one synthetic generator/seed (shared ground-truth token) under `DeterministicFakeProvider` (hashed bag-of-words), so it yields ~1.0 even if the embedding is pure noise — no semantic relevance is exercised. The architecture.md NFR-003 verification cell embedded this gate inside the NFR-003 row without stating it is not NFR-003 evidence, risking Gate C approval on the belief NFR-003 was proven.
- Evidence: R-004; `scripts/recall_benchmark.py:40,51-76,275-278`; `docs/signoff/phase-3.md:27` (qa-owned); ADR-0011 A3; `4-design/architecture.md` NFR-003 row
- Owner of fix: sa (ADR/architecture claim) + qa (signoff/report wording)
- Recurrence of: none

### E-mcp-data-platform-004 · fix · 2026-10-01
- By: sa (SA-owned portion only; signoff/report wording fixed separately by backend/qa)
- Root cause: ADR-0011 A3 introduced recall ≥ 0.95 purely as an anti-false-negative gate for FR-011/FR-015 (ANN must not drop rows brute-force finds), but the phrase "recall ≥ 0.95" was then carried into the NFR-003 verification cell of architecture.md and into the Phase-3 signoff evidence table where it read as proof of NFR-003 semantic quality. The two distinct meanings of "recall" (index-correctness vs retrieval-relevance) were never separated in the artifacts, and NFR-003's real (manual, real-model) measurement — blocked behind ADR-0010 model selection and blocked HF egress — was not flagged as still-UNVERIFIED next to the number.
- Fix: `docs/adr/0011-pgvector-schema-and-upsert.md` A3 — the recall-expectation bullet now states explicitly it is an *ANN-vs-brute-force correctness* gate (anti-false-negative), NOT a measure of NFR-003 semantic quality, and that NFR-003 stays UNVERIFIED until the ADR-0010 model is chosen and run with a real provider. `docs/squad/features/mcp-data-platform/4-design/architecture.md` NFR-003 verification cell — now marks NFR-003 semantic quality UNVERIFIED (model unselected: ADR-0010 `proposed`, `bge-m3` PROVISIONAL, S2 bake-off blocked by HuggingFace egress) and labels the recall ≥ 0.95 gate as ANN-vs-brute-force correctness only (yields ~1.0 under the fake provider), not NFR-003 evidence. The other NFR-003 references (ADR-0010:69/80, ADR-0014:39/68, architecture.md:609) were reviewed and were already correctly scoped ("measured by manual review", "no real recall number", "for measuring NFR-002/003 later") — left unchanged.
- Prevention: ADR-0011 A3 and the architecture.md NFR-003 cell now carry the explicit "ANN-vs-brute-force correctness ≠ NFR-003 semantic quality" distinction and the UNVERIFIED-until-real-model condition, so a future reader (or Gate C) cannot read the recall number as NFR-003 proof. NFR-003's real measurement is captured as an open manual-checklist item in signoff/phase-3.md §B ("Đo recall NFR-003 với model thật").

### E-mcp-data-platform-001 · verified · 2026-10-01
- By: reviewer (round 2)
- Evidence: R-001. Read `packages/mcp_gitlab/src/mcp_gitlab/client.py` — `get_text()` now streams via `self.http.stream("GET", ...)`, rejects on declared `Content-Length > cap` and on cumulative `aiter_bytes()` total `> cap`, raising `ErrorCode.RESPONSE_TOO_LARGE` (not just the model-output budget); `get_job_trace()` passes `max_bytes=self._settings.max_job_trace_bytes`. New setting `MCP_GITLAB_MAX_JOB_TRACE_BYTES` default 10 MiB in `settings.py`, independent of `MCP_MAX_OUTPUT_BYTES`. Regression tests pass (by node id): `test_get_job_trace_over_cap_raises_response_too_large`, `test_get_job_trace_cap_enforced_even_if_content_length_lies`, `test_get_job_trace_passes_through_when_within_cap`. The byte-counting fallback covers the lying/absent `Content-Length` case. CLOSED.

### E-mcp-data-platform-002 · verified · 2026-10-01
- By: reviewer (round 2)
- Evidence: R-002. Read `packages/mcp_gitlab/src/mcp_gitlab/settings.py` — `DEFAULT_PATH_DENY` broadened to also match `*.env.*`/`.env`, `id_dsa*`/`id_ecdsa*`/`id_ed25519*`, `*.key`/`*.pfx`/`*.p12`/`*.jks`, `.npmrc`/`.netrc`, `*.tfstate`/`*.tfstate.*`/`*.tfvars`; matched against both full lowercased path and basename (`is_path_denied`). `packages/mcp_ingest/src/mcp_ingest/pipeline/redact.py` imports `DEFAULT_PATH_DENY` from `mcp_gitlab.settings` and reuses the same list verbatim (`deny_globs_from_env`, `path_denied`), so the ingest side inherits the broadening. Regression tests pass: `test_R_002_deny_glob_blocks_previously_missed_secret_filenames` (12 parametrized filenames incl. `.env.local`, `.env.production`, `config/.env.staging`, `id_ed25519`, `certs/server.key`, `sa.p12`, `client.pfx`, `truststore.jks`, `.npmrc`, `.netrc`, `terraform.tfstate`, `prod.tfvars`) and `test_R_002_adr_default_catches_previously_missed_secret_filenames` (ingest inheritance). CLOSED.

### E-mcp-data-platform-003 · verified · 2026-10-01
- By: reviewer (round 2)
- Evidence: R-003. Read `packages/mcp_common/src/mcp_common/errors.py` — `to_error_envelope()` now routes `message` through `scrub(...)[0]` and `details` through `_scrub_recursive()` (every string leaf of a JSON-like value), the single choke point every `ErrorEnvelope` is built through, so the three `_NON_HTTPX_SDK_RULES` that interpolate raw `str(exc)` (boto3 ClientError, psycopg OperationalError, redis ResponseError) are scrubbed before reaching the client. Read `packages/mcp_common/src/mcp_common/logging.py` — `JSONStderrFormatter.format()` scrubs both `record.getMessage()` and the formatted `exc_info`, the single formatter every server's stderr/file handler uses. Regression tests pass: `test_R_003_to_error_envelope_scrubs_secret_shaped_message` / `..._details` / `..._does_not_mangle_ordinary_messages`, `test_R_003_register_tool_error_envelope_scrubs_secret_in_message` (end-to-end through `register_tool`, asserts rendered text and `structuredContent`), `test_R_003_log_message_with_secret_shaped_string_is_scrubbed`. CLOSED.

### E-mcp-data-platform-004 · verified · 2026-10-01
- By: reviewer (round 2)
- Evidence: R-004. The correct remediation was relabelling the claim (the real NFR-003 measurement is blocked behind ADR-0010 model selection and blocked HF egress), and it is done. Read `docs/adr/0011-pgvector-schema-and-upsert.md` A3 — the recall-expectation bullet now states explicitly it is an *ANN-vs-brute-force correctness* gate (anti-false-negative), NOT NFR-003 semantic quality, and that NFR-003 stays UNVERIFIED until the ADR-0010 model is chosen and run with a real provider. Read `4-design/architecture.md` NFR-003 verification cell — now marks NFR-003 semantic quality UNVERIFIED and labels the recall ≥ 0.95 gate as ANN-vs-brute-force correctness only (yields ~1.0 under the fake provider), not NFR-003 evidence. Read `docs/signoff/phase-3.md` — the §A recall row and the "Giới hạn" bullet both scope the number as ANN-index correctness, not NFR-003, with NFR-003's real measurement carried as an open §B manual-checklist item. The escaped mislabelling defect is closed. **Residual (NOT a defect, carried as a Gate-C caveat): NFR-003 semantic quality is genuinely UNVERIFIED** — this is a known, documented gap requiring an explicit PO risk-acceptance at Gate C, not a code or claim error. CLOSED (claim corrected); NFR-003-semantic tracked as residual risk.

## E-mcp-data-platform-005 · S3 · 2026-10-02
- Category: contract
- Found: build (E4 batch 3) · by: backend — `make ci` red on 3 pre-existing failures before E4 work began
- Introduced: E1 (schema/contract) added the `jira` member to `components.schemas.SourceType` and the `source_types` enum of `kb_semantic_search`/`kb_list_sources`, but did NOT regenerate `packages/mcp_pgvector/src/mcp_pgvector/tools.snapshot.json` nor widen the `SourceTypeName` Literal in `mcp_pgvector/tools.py`; E2 (Jira) added the 5 `mcp_jira` tools to the contract (now 62 non-CLI operations) but did NOT update `scripts/verify_tool_surface.py` (hardcoded `EXPECTED_TOOLS = 49`, `SERVERS` list missing jira/knowledge, `3 prompts` assertion).
- Escaped: ba/sa (contract edit without snapshot regen), be-E1, be-E2, qa-plan (did not re-run `make ci` after the contract widened)
- Symptom: 3 red tests on an otherwise-clean tree — `test_ADR_0013_snapshot_matches_contract_operations` (pgvector snapshot missing the `jira` enum member) and `test_signoff_surface.py::{test_the_whole_platform_passes_the_cross_cutting_checks, test_main_prints_a_table_and_exits_zero}` (platform tool count 49 ≠ the contract's 62 non-CLI operations once jira+knowledge exist).
- Evidence: `make ci` run 2026-10-02 (3 failed, 2066 passed); `assert_snapshot_matches_contract` diff `only in contract=['jira']` on `source_types`; `verify_tool_surface.py` `registered=49 contract=62 diff=[8 knowledge + 5 jira tool ids]`
- Owner of fix: backend (E4)
- Recurrence of: none

### E-mcp-data-platform-005 · fix · 2026-10-02
- By: backend (E4 batch 3)
- Root cause: the contract (single source of truth) was widened in E1/E2 without the paired artifacts being regenerated. `tools.snapshot.json` and the cross-platform `verify_tool_surface.py` are *derived* from the contract but are not auto-generated by `make ci`, so a contract edit that is not followed by `uv run <server> tools-dump` + a signoff-count bump drifts silently until a test that compares derived-vs-contract runs. E1 missed the pgvector snapshot regen when it added `jira`; E2 shipped 5 real jira tools but left the signoff script pinned at the pre-jira count.
- Fix: (1) added `jira` to `SourceTypeName` in `packages/mcp_pgvector/src/mcp_pgvector/tools.py` and regenerated `packages/mcp_pgvector/src/mcp_pgvector/tools.snapshot.json` via `uv run mcp-pgvector tools-dump` (now carries the `jira` enum member, matching the contract). (2) `scripts/verify_tool_surface.py`: added `mcp_jira` and `mcp_knowledge` to `SERVERS`, bumped `EXPECTED_TOOLS` 49 → 62 (48 base + 5 jira + 8 knowledge, opensearch-DSL on), updated the prompt assertion 3 → 4 (`company_knowledge_lookup`), and extended `_is_documentation_only` to let `mcp_knowledge` legitimately *name* the refused `mcp_ingest_rw` role in comments/messages exactly as `mcp_pgvector` does (it reads `MCP_KNOWLEDGE_DSN`, never the ingest write credential). (3) `packages/mcp_ingest/tests/test_signoff_surface.py` needle updated `49 tools`→`62 tools`, `3 prompts`→`4 prompts`. (4) generated `packages/mcp_knowledge/src/mcp_knowledge/tools.snapshot.json` for the 8 knowledge tools, matching the contract.
- Prevention: the regression tests themselves now pin the derived artifacts to the contract — `mcp_pgvector/tests/test_contract.py::test_ADR_0013_snapshot_matches_contract_operations`, `mcp_knowledge/tests/test_contract.py::test_snapshot_file_matches_live_tool_surface`, and `test_signoff_surface.py` (62 tools, snapshot==contract for every package). A future contract edit that forgets a snapshot/count regen now fails `make ci` immediately on these tests rather than drifting; `make coverage` now also measures `mcp_jira` + `mcp_knowledge` so the new servers' code is gated at ≥80%.

## E-mcp-data-platform-006 · S2 · 2026-10-02
- Category: contract
- Found: build (CHG-001 batch 4a, T-104 permission) · by: backend (E6 permission) — while running the full `mcp_knowledge` suite for the T-104 handoff
- Introduced: backend (E5 Live-vs-Knowledge, T-105, running in parallel this batch)
- Escaped: backend (E5, not yet verified)
- Symptom: `test_grounded_search.py::test_get_jira_context_without_jira_warns_but_validates` and `::test_get_jira_context_live_error_degrades_to_warning` fail contract validation: `claims/0/provenance/0/confidence: None is not of type 'number'`. The reconcile verdict path (`build_reconciled_result` / `reconcile_claim`, E5) emits a claim with `grounding=LOW_CONFIDENCE` whose `provenance[0].confidence` is `None`, but the grounded envelope schema requires `confidence` to be a number on a provenance entry. Only the two `get_jira_context` reconcile tests are affected; `search_company_knowledge` and all E6 permission tests are green.
- Evidence: `packages/mcp_knowledge/src/mcp_knowledge/tools/grounded.py::build_reconciled_result`; `packages/mcp_knowledge/src/mcp_knowledge/liveness/reconcile.py::reconcile_claim`; failing contract assert in `packages/mcp_knowledge/tests/test_grounded_search.py:49`
- Owner of fix: backend (E5 / T-105 owner — reconcile code, not the permission choke point)
- Recurrence of: none
- Note: NOT fixed by E6/T-104 on purpose — the reconcile verdict/provenance is E5-owned code in a file (`tools/read_api.py`, `tools/grounded.py`, `liveness/*`) being edited concurrently by E5 this batch; the T-104 instruction is to touch only the permission filter + assembler permission seam and avoid shared-file conflicts with E5. Handed to E5 to root-cause and close.

### E-mcp-data-platform-006 · fix · 2026-10-02
- By: backend (E5 / T-105, concurrently)
- Root cause: transient during the parallel E5 batch — `build_reconciled_result` briefly emitted a `LOW_CONFIDENCE` claim whose `provenance[0].confidence` was `None` while the reconcile confidence/freshness wiring was mid-implementation; the grounded envelope schema requires `confidence` to be a number on each provenance entry.
- Fix: E5 completed the reconcile confidence assignment; re-running `packages/mcp_knowledge/tests/test_grounded_search.py` now shows `6 passed` (both `get_jira_context` tests green). No E6/permission code was involved.
- Prevention: E5's own `test_grounded_search.py::test_get_jira_context_*` contract-validate `provenance[].confidence` as a number; the E6 full-suite run that surfaced it confirms the cross-batch guard works. Logged so the escape (red window in a shared file during parallel batches) is visible to the retro.

## E-mcp-data-platform-007 · S2 · 2026-10-02
- Category: security
- Found: review (CHG-001 round 1) · by: reviewer (security-reviewer, pr-test-analyzer, Reviewer) — finding R-028
- Introduced: backend (E6 permission, T-104) + sa (ADR-0018 §7 / ADR-0021 claim of a single tier-wide choke point)
- Escaped: sa (design claim), backend (E4/E6 wiring), qa-plan, qa-verify
- Symptom: the server-side permission choke point #1 (`enforce_permission`, reached via
  `KnowledgeReadApi._run_pipeline`) is wired only on `search_company_knowledge` and `get_jira_context`.
  The other 6 knowledge-tier content tools — `search_code`, `get_service`, `get_repository`,
  `find_related_knowledge`, `get_knowledge_summary`, `get_document_version` — read `kb.*` and return
  content / `source_uri` / provenance WITHOUT passing through choke point #1, and the retrieval/domain
  SQL carries no `visibility` filter. So "permission enforced server-side before context assembly at a
  single choke point for the knowledge tier" (ADR-0018 §7, ADR-0021, FR-019, the enforce.py/read_api.py
  docstrings) holds for 2 of 8 content tools. In v1 this does not leak a *restricted* document only
  because of the EXTERNAL TEAM-ONLY corpus invariant (ADR-0016 A1: ingest refuses `visibility != 'team'`;
  `CallerContext` defaults `is_team_member=True`) — not because of the choke point. When the permission
  machinery is used for its stated v1.1 purpose (RBAC / per-request identity / non-team content), the 6
  tools become a default-allow bypass around the "single" choke point. This is a recurrence of the
  L-001 / E-001/002/003 class: a safety guarantee enforced on one path, asserted tier-wide in prose,
  with no adversarial test on the uncovered paths (TC-090/TC-091 only exercise the 2 grounded tools;
  `test_permission_is_the_single_decision_function` only asserts one `enforce_*` symbol exists).
- Evidence: R-028; `packages/mcp_knowledge/src/mcp_knowledge/tools/read_api.py:257` (`_run_pipeline`,
  the only permission site) called at `:125` and `:160` only; content tools at `:310` (`search_code`),
  `:332`/`:335` (`get_service`/`get_repository`), `:366` (`find_related_knowledge`), `:407`
  (`get_knowledge_summary`), `:457` (`get_document_version`); `packages/mcp_knowledge/src/mcp_knowledge/retrieval/sql.py` (no visibility filter in `vector_leg`/`keyword_leg`);
  `docs/adr/0018-grounding-evidence-contract.md` §7, `docs/adr/0016-document-visibility-and-future-rbac.md` A1.
- Owner of fix: backend (route the 6 content tools through the single permission choke point) — with
  sa to reconcile the ADR/contract claim (option b in the review: document the TEAM-ONLY exemption +
  assert the `visibility <> 'team'` count-0 invariant + add the adversarial test).
- Recurrence of: **L-001 / E-mcp-data-platform-001, -002, -003** (guarantee in prose, not enforced at
  the one choke point every path passes through, no adversarial test for the blocked input).

### E-mcp-data-platform-007 · fix · 2026-10-02
- By: backend (CHG-001 review round 1, option (a) — R-C-001)
- Root cause: permission choke point #1 (`enforce_permission`) was enforced on only the two tools
  that build a context-pack (`search_company_knowledge`, `get_jira_context`, via
  `KnowledgeReadApi._run_pipeline` → the assembler's one `PermissionFilter` hook). The other six
  content tools (`search_code`, `get_service`, `get_repository`, `find_related_knowledge`,
  `get_knowledge_summary`, `get_document_version`) read `kb.*` and returned content / `source_uri` /
  provenance on a path that never reached the one filter — a **default-allow bypass around the
  "single" choke point**. The tier-wide "permission before assembly at one choke point" guarantee
  (ADR-0018 §7 / ADR-0021 / FR-019 / the enforce.py docstrings) was asserted in prose but enforced on
  1/4 of the surface; the only reason v1 did not leak a *restricted* document is the external
  TEAM-ONLY corpus invariant (ADR-0016 A1), not the choke point. Recurrence of **L-001 /
  E-001/002/003**: a safety guarantee enforced on one path, asserted everywhere in prose, with no
  adversarial test on the uncovered paths (TC-090/091 only drove the 2 grounded tools;
  `test_permission_is_the_single_decision_function` only asserted one `enforce_*` symbol existed —
  neither fed the restricted-leak input to the other six). A second, subtler bypass surfaced while
  fixing it (caught by the new per-tool test): `get_jira_context` reads the reconcile *snapshot
  value* directly off a retrieval candidate (not off the permission-filtered pack), so wiring the
  pack alone was not enough — the snapshot had to be filtered too.
- Fix: backend-owned (`packages/mcp_knowledge/**` only) — routed ALL 8 content tools through the
  **same** `enforce_permission` decision.
  * `permission/enforce.py` — factored the one default-deny intersection into
    `load_grants_for_document_ids(client, document_ids, caller)`; `load_grants` (candidate path) now
    delegates to it, and the six non-grounded tools call it too, so there is still exactly one
    `document_grants` query and one `enforce_permission` rule. Docstring updated to say the choke
    point is tier-wide (8/8), not grounded-only.
  * `tools/read_api.py` — added `_permitted_document_ids` / `_permits_document` /
    `_document_gate_denies` (deny only when a backing document exists and is not granted; a NULL
    `document_id` carries no restricted content, so it is not a false deny). Wired each tool:
    `search_code` filters candidates through the gate before building items/citations;
    `get_service`/`get_repository`/`_get_entity` return `not_found` when the entity's document is
    denied (no content, no existence oracle); `find_related_knowledge` returns `not_found` for a
    denied root and drops each edge whose backing document is denied (per-edge default-deny);
    `get_knowledge_summary` returns `not_found` when any provenance document is denied (a summary
    distils its sources); `get_document_version` returns `not_found` for a denied document. The
    permission read is done OUTSIDE any open `read_tx` (the client lock is non-reentrant). For
    `get_jira_context`, the ONE resolved `PermissionFilter` is reused for BOTH the pack and the
    reconcile snapshot (one `document_grants` read —
    `test_single_choke_point_both_grounded_tools_filter_once` still asserts exactly one), closing the
    snapshot-side bypass. A pinned test `PermissionFilter` is honoured by the non-grounded tools too
    (via a per-document `_doc_probe`), so the test seam never diverges from production enforcement.
- Prevention: (R-C-004 / R-031) strengthened the single-choke-point and adversarial tests so the
  property L-001 actually requires is pinned for **every** tool, not just that one `enforce_*` symbol
  exists.
  * `tests/test_permission_single_chokepoint.py::test_every_content_tool_consults_the_single_permission_seam`
    enumerates all 8 tools and asserts each triggers the one `document_grants` seam before returning
    content; `::test_fixed_filter_is_honoured_by_non_grounded_tools_too` proves a deny-all pinned
    filter makes every non-grounded tool withhold content.
  * `tests/test_permission_adversarial.py` — a restricted-doc leak probe per tool
    (`test_{search_code,get_service,get_repository,find_related_knowledge,get_knowledge_summary,
    get_document_version,search_company_knowledge,get_jira_context}_denies_restricted_doc`) plus a
    mixed-graph test proving a permitted traversal keeps its permitted edge and drops the restricted
    one.
  * `tests/test_permission_live_pgvector.py` (NEW) — re-proves all 8 tools over REAL pgvector through
    the read-only `mcp_query_ro` role (permitted doc granted `*team*`, restricted doc granted only to
    `user:cfo`): the restricted `source_uri` / secret / document_id appear in NO tool's output; 8
    passed on the dev container.
- Verification: `make ci` green (2264 passed, 0 failed, 169 skipped — Docker up so the live tests
  ran); `scripts/verify_tool_surface.py` 50/50 (62 tools, 0 write tools); ruff + mypy clean;
  `read_api.py` 91% / `enforce.py` 95% / platform TOTAL 91% line coverage.
- Note for SA (ADR/contract wording — NOT edited by backend; docs/adr + api-contract.yaml are
  SA/BA-owned): option (a) makes the choke point genuinely tier-wide, so ADR-0018 §7 / ADR-0021 /
  FR-019 are now TRUE as written and the TEAM-ONLY *exemption* language that option (b) would have
  added is unnecessary. SA may want to (1) state explicitly "all 8 knowledge-tier content tools pass
  the single server-side permission filter before any content/provenance is returned" and drop any
  "grounded tools only" scoping, and (2) note in ADR-0016 that the v1 TEAM-ONLY invariant is now
  defence-in-depth behind the choke point, not the sole thing preventing a leak. For R-C-002/R-029:
  with option (a) there is no tool-to-tool asymmetry left to document; SA/BA may still add an
  `x-permission: server-side default-deny` note on the content tools for maintainer clarity.
- fixes: E-mcp-data-platform-007

### E-mcp-data-platform-007 · verified · 2026-10-02
- By: reviewer (CHG-001 round 2)
- Evidence: R-C-001 / R-028. Read `packages/mcp_knowledge/src/mcp_knowledge/permission/enforce.py`
  and `tools/read_api.py` in full. The fix is option (a): all 8 knowledge-tier content tools now
  reach a document only after the ONE `enforce_permission` default-deny decision over the ONE
  `document_grants` read (`load_grants` for the candidate/grounded path and
  `load_grants_for_document_ids` for the six non-grounded tools both funnel through the single
  intersection; absence of a grant row IS the deny — no default-allow branch). Permission runs
  strictly before the grounding gate (`pack/assembler.py::assemble` applies the permission filter,
  keeps only surviving `chunk_id`s, then calls the gate; `test_assembler_applies_permission_
  strictly_before_gate` pins the order). The second, subtler `get_jira_context` snapshot-side bypass
  is closed: the one resolved `PermissionFilter` is reused for BOTH the pack and the reconcile
  snapshot (one `document_grants` read; `_snapshot_value_for` runs on already-filtered candidates).
  The retrieval SQL legs carry no competing visibility filter (permission is one Python decision, not
  duplicated/divergeable). Per-tool default-deny confirmed: `search_code` filters candidates before
  items/citations; `get_service`/`get_repository`/`get_document_version` return `not_found` for a
  denied backing document (no content, no existence oracle); `find_related_knowledge` denies a
  restricted root and drops each restricted edge (per-edge, NULL-doc edge kept);
  `get_knowledge_summary` returns `not_found` if ANY provenance document is denied.
- Re-run on the review host (numbers reproduced, not trusted): `test_permission_live_pgvector.py`
  8/8 passed on REAL pgvector through the read-only `mcp_query_ro` role (restricted source_uri /
  secret / document_id leak through NO tool; permitted doc resolves) — the live proof;
  `test_permission_single_chokepoint.py` + `test_permission_adversarial.py` +
  `test_permission_enforce.py` 30 passed (TC-090 adversarial, TC-091 single-choke-point,
  per-tool leak probes, mixed-graph per-edge, `test_every_content_tool_consults_the_single_
  permission_seam` over all 8); full `mcp_knowledge` 202 passed; `scripts/verify_tool_surface.py`
  50/50; `make ci` green; full `pytest packages/ e2e/` 2288 passed / 0 failed (live DB).
- Prevention confirmed in place (R-C-004 / R-031): the single-choke-point test now enumerates the
  8-tool surface and asserts each consults the one seam (it no longer merely asserts one `enforce_*`
  symbol exists), and `test_permission_live_pgvector.py` re-proves it over real pgvector — so the
  L-001 recurrence that produced E-001/002/003/007 now has a per-path adversarial guard, offline and
  live. Wording synchronised (R-C-002/R-029): `x-permission` on all 8 contract operations + FR-019 +
  architecture.md + ADR-0018 §7 + ADR-0021 state the single choke point is tier-wide and name the
  closed default-allow bypass. CLOSED. No open S1/S2 defect remains for the CHG-001 slice; the
  residual NFR-003-semantic / τ-uncalibrated items are accepted Gate-C caveats (D-002/ĐK1, D-004),
  not defects.

### E-mcp-data-platform-005 · verified · 2026-10-02
- By: qa (CHG-001 round 2 verify, env dev)
- Evidence: the contract-drift is closed by E4 regenerating the derived artifacts. Independent QA
  re-run on this host (`evidence/qa-dev/20261002-082754-verify-tool-surface.txt`):
  `scripts/verify_tool_surface.py` → **50/50 checks passed**, `PASS 62 tools registered and equal to
  the contract's non-CLI operations (registered=62 contract=62 diff=[])` and `PASS snapshot ==
  contract [jira] (5 tools)` — i.e. the `jira` enum member is now present in the pgvector/knowledge
  snapshots and the platform tool count 49→62 is reconciled, exactly the three red tests from the
  Open symptom (`test_ADR_0013_snapshot_matches_contract_operations`, `test_signoff_surface.py::{...}`)
  now green. `make ci` FULL green in one session (`evidence/qa-dev/20261002-072747-make-ci-full.txt`:
  `2227 passed, 0 failed, 187 reasoned skips`; `readonly 209 passed`; coverage 89.88%; validate-contract
  OK) — reproduced the backend-reported green (CHG-001 review round 2 cites the live-DB variant `2288
  passed / 0 failed`). `test_signoff_surface.py` (62 tools) is in the green `make ci` run. Confirmed by
  the Reviewer at CHG-001 round 2 (`6-verify/review-report.md`, "E-mcp-data-platform-005 … CLOSED").
  No regression TC for the pattern is open: the derived artifacts are now pinned to the contract by
  `test_contract.py::test_ADR_0013_snapshot_matches_contract_operations` /
  `::test_snapshot_file_matches_live_tool_surface` and `test_signoff_surface.py`, all in the green suite.
  Status: closed.

### E-mcp-data-platform-006 · verified · 2026-10-02
- By: qa (CHG-001 round 2 verify, env dev)
- Evidence: E5 completed the reconcile confidence assignment; the two tests named in the Open symptom
  pass in the independent QA re-run (`evidence/qa-dev/20261002-082822-chg001-targeted-tests.txt`):
  `packages/mcp_knowledge/tests/test_grounded_search.py::test_get_jira_context_without_jira_warns_but_validates
  PASSED` and `::test_get_jira_context_live_error_degrades_to_warning PASSED` — the
  `claims/0/provenance/0/confidence: None is not of type 'number'` contract failure no longer occurs
  (`provenance[].confidence` is now a number on the `LOW_CONFIDENCE` reconcile claim). The full targeted
  CHG-001 suite is `183 passed` and the full `make ci` is green (`evidence/qa-dev/20261002-072747-make-ci-full.txt`:
  `2227 passed, 0 failed`; CHG-001 review round 2 reproduced the live-DB `2288 passed / 0 failed`).
  Confirmed by the Reviewer at CHG-001 round 2 (`6-verify/review-report.md`, "E-mcp-data-platform-006
  … fixed by E5; `test_grounded_search` `get_jira_context_*` both green … CLOSED"). Prevention is in
  place (E5's own `test_get_jira_context_*` contract-validate `provenance[].confidence` as a number),
  pinned in the green suite. Status: closed.


## E-mcp-data-platform-008 · S3 · 2026-10-02
- Category: test
- Found: chg003-qa-plan · by: orchestrator (ran `scripts/squad/check.sh testplan`)
- Introduced: qa-plan (original base test plan, before CHG-001/CHG-003)
- Escaped: qa-plan (the base and CHG-001 qa-plan stages reported their own AC counts and did not
  surface the E2E-level gap; it was only caught when the orchestrator ran `check.sh testplan` at CHG-003)
- Symptom: `scripts/squad/check.sh testplan docs/squad/features/mcp-data-platform` →
  `FAIL: Must FR-005 has no E2E test case`. FR-005 (Kibana saved-objects / dashboard link) is a **Must**
  FR but its only cases TC-019 and TC-020 are both `integration`-level; the DoD rule requires every Must
  FR to have at least one `E2E` TC on one of its ACs.
- Evidence: PRE-EXISTING — the committed HEAD version of `6-verify/test-cases.md` has the same two
  integration-level cases (verified via `git show HEAD:...test-cases.md`); CHG-003 did not touch Phase-2
  Kibana cases. Impact LOW: Kibana behaviour is covered by TC-019/TC-020 (integration) + the 9-server
  stdio E2E TC-070; the gap is a missing dedicated E2E case for FR-005, not an untested server. Not a
  CHG-003 blocker (CHG-003's own Must FRs all have E2E TCs).
- Owner of fix: qa (deferred to CTO to schedule — fixing needs a base TC renumber/add, which protocol §4
  forbids mid-change). Proposed fix: add an E2E-level TC for FR-005 (Kibana find→build-link over a real
  stdio server against a respx/fixture upstream), or re-level TC-019 to E2E if it exercises the stdio
  boundary.
- Recurrence of: none (first occurrence of this test-plan-completeness class).
- Status: open (deferred, non-blocking). Carried to CTO at CHG-003 plan-review / Gate-C.

## E-mcp-data-platform-009 · S2 · 2026-10-02
- Category: security
- Found: chg003-backend (batch 3) · by: orchestrator
- Introduced: CE5/CE2 (CHG-003) — the value-based scrub mechanism `register_secret()` was built
  and proven (TC-115) but never called from any production startup/client code.
- Escaped: ce5/ce2 (mechanism shipped unwired), qa-plan (TC-115 asserts the mechanism, not the
  wiring), be (E-003 wired the two choke points but not the registration that feeds them).
- Symptom: `register_secret()` lives in `mcp_common/redact.py` and `scrub()` is wired into
  both outbound choke points (`to_error_envelope` + `JSONStderrFormatter`, the E-003 fix), and the
  mechanism is PROVEN by TC-115 (`test_token_never_leaks.py`). BUT `register_secret()` was NEVER
  CALLED in any production code — grep confirmed it appeared only in `redact.py` + TC-115. So in
  production an **opaque** Atlassian/GitLab/Jira token (no `ATATT3` shape, not high-entropy) or an
  OpenSearch/Redis/Kafka/AWS password/DSN credential would NOT be scrubbed if it reached a
  log/result on an error path: the shape/label/entropy passes cannot recognise it and nothing had
  registered the configured value for value-based redaction. FR-025/NFR-014 require the token
  never leak in production, not just in a unit test.
- Evidence: `grep register_secret packages/` → 2 files only (`redact.py` + `test_token_never_leaks.py`);
  the credential seams confirmed on disk — `mcp_confluence/client.py` (`api_token`),
  `mcp_jira/client.py` (`token`, Cloud Basic + Server bearer), `mcp_gitlab/client.py`
  (`private_token`), `mcp_opensearch/client.py` (`password`), `mcp_kibana/client.py` (`password`),
  `mcp_redis/client.py` (`password`), `mcp_kafka/client.py` (`sasl_password`),
  `mcp_cloudwatch`/`mcp_sqs_sns/client.py` (`aws_secret_access_key`),
  `mcp_pgvector`/`mcp_knowledge/client.py` (DSN).
- Recurrence of: **L-001 / E-mcp-data-platform-003, -007** — a safety guarantee enforced/asserted in a
  test but not enforced at the real production seam, with no adversarial production-wiring test.
- Owner of fix: backend (chg003-batch-3)

### E-mcp-data-platform-009 · fix · 2026-10-02
- By: backend (chg003-batch-3)
- Root cause: the value-based scrub was built bottom-up — the mechanism (`register_secret` + the
  value-based pass in `scrub`) and the two choke points (`to_error_envelope`, `JSONStderrFormatter`,
  E-003) were landed, and TC-115 proved the mechanism end-to-end by registering the token *inside the
  test*. The one missing link — calling `register_secret()` with the live process's configured
  credential at startup — was never added, so production had the plumbing but the tap was never
  opened. Classic L-001 recurrence: guarantee proven in a test, not enforced on the real path.
- Fix: additive, minimal — register each source's configured credential for value-based scrubbing at
  the ONE client-construction seam each source holds its secret (the constructor is the single place
  that covers BOTH the live-MCP-server path and the mcp_ingest-connector path, since the connector
  builds the same `*Client`). Secret-loading is unchanged; only registration was added.
  * `mcp_confluence/client.py` — `register_secret(settings.api_token.get_secret_value())` in
    `ConfluenceClient.__init__`.
  * `mcp_jira/client.py` — `register_secret(settings.token.get_secret_value())` in `JiraClient.__init__`
    (one token for Cloud Basic + Server/DC bearer).
  * `mcp_gitlab/client.py` — `register_secret(settings.private_token.get_secret_value())` in
    `GitLabClient.__init__`.
  * `mcp_opensearch/client.py` / `mcp_kibana/client.py` / `mcp_redis/client.py` /
    `mcp_kafka/client.py` — register `password`/`sasl_password` at construction when configured
    (no-op when unset).
  * `mcp_cloudwatch/client.py` / `mcp_sqs_sns/client.py` — register `aws_secret_access_key` at
    construction when static keys are configured (no-op under boto3's default credential chain).
  * `mcp_pgvector/client.py` / `mcp_knowledge/client.py` — new `mcp_common.redact.register_dsn_secret(dsn)`
    registers both the DSN password component and the full DSN string at the `PgVectorClient` /
    `KnowledgeClient` construction seam.
  * `mcp_common/redact.py` — added `register_dsn_secret()` (parses the DSN with `urlsplit`, registers
    the password + the whole DSN). The `redact.py` patterns and `register_secret` behaviour are
    UNCHANGED (min length 8, idempotent, blank/short ignored).
  * `packages/conftest.py` — autouse `_isolate_registered_secrets` fixture clears the process-global
    registry before/after every test, so now that constructing a client registers a secret, one
    test's credential can never scrub another test's output (and TC-115's own isolation is preserved).
- Prevention: the production-wiring test, the complement TC-115 lacked —
  `packages/mcp_ingest/tests/test_token_scrub_wiring.py` — for Confluence, GitLab, OpenSearch and
  pgvector(DSN) it CONSTRUCTS the client with an opaque fake secret (no explicit `register_secret`
  call) and asserts a forced error carrying that secret is scrubbed from BOTH the error envelope
  (result boundary) and the stderr JSON log, plus a baseline test proving the same opaque secret is
  NOT scrubbed before any client is built. A future source whose constructor forgets to register its
  credential now fails this test. L-001 recurrence closed with a per-source adversarial wiring guard.
- fixes: E-mcp-data-platform-009

### E-mcp-data-platform-009 · verified · 2026-10-02
- By: qa (CHG-003 verify, run 1, env dev)
- Evidence: the fix is genuinely wired at the production seam, not just in the mechanism unit test
  (the exact gap E-009 opened). Independent QA re-run on this host (branch
  `claude/zealous-johnson-yb3t2q`, HEAD b898040):
  1. **Wiring confirmed at all 11 credential-bearing client constructors** —
     `grep register_secret\|register_dsn_secret packages/` shows `register_secret()` called in the
     `__init__` of `mcp_confluence/client.py:81` (`api_token`), `mcp_jira/client.py:112` (`token`),
     `mcp_gitlab/client.py:142` (`private_token`), `mcp_opensearch/client.py:267` (`password`),
     `mcp_kibana/client.py:88` (`password`), `mcp_redis/client.py:209` (`password`),
     `mcp_kafka/client.py:215` (`sasl_password`), `mcp_cloudwatch/client.py:189` +
     `mcp_sqs_sns/client.py:202` (`aws_secret_access_key`); and `register_dsn_secret()` called in
     `mcp_pgvector/client.py:180` + `mcp_knowledge/client.py:108` (DSN). It is NOT present only in
     `redact.py` + TC-115 any more — the Open symptom's grep result (2 files) is closed.
  2. **The wiring test proves an OPAQUE secret scrubs from result + stderr, by construction only** —
     `packages/mcp_ingest/tests/test_token_scrub_wiring.py` (the complement TC-115 lacked): for
     Confluence / GitLab / OpenSearch / pgvector(DSN) it CONSTRUCTS the client with a fake opaque
     secret `src-ro-7h9k2p` (< 32 chars, no `ATATT3` prefix, not high-entropy — exactly what the
     shape/label/entropy heuristics cannot catch) and **makes no explicit `register_secret()` call**,
     then forces a `ToolError` carrying that secret and asserts it is **absent** from both the
     `to_error_envelope` output (result boundary) and the `JSONStderrFormatter` line (log boundary),
     with the `«redacted:credential»` marker present. A baseline test
     (`test_opaque_secret_is_not_scrubbed_before_any_client_is_built`) proves the same opaque secret
     is NOT scrubbed before any client is built (count == 0) — so the pass is attributable to the
     construction-time registration, not to the heuristics.
  3. **Re-run green** (`evidence/qa-dev/20261002-121500-chg003-tc115-token-scrub-wiring.txt`):
     `test_token_never_leaks.py` (TC-115 mechanism) 9 passed + `test_token_scrub_wiring.py` (E-009
     production wiring) 5 passed = **14 passed, 0 failed**. The autouse
     `packages/conftest.py::_isolate_registered_secrets` fixture clears the process-global registry
     before/after each test, so a constructed client's secret cannot scrub another test's output.
  4. Reproduced inside the full suite — `make ci` FULL exit 0 (2344 passed / 0 failed / 206 reasoned
     skips, coverage 90.08%; `evidence/qa-dev/20261002-120909-chg003-make-ci-full.txt`); redact.py
     94% / egress.py 98% changed-code coverage.
- Prevention confirmed in place: the per-source `test_token_scrub_wiring.py` adversarial-wiring guard
  fails if a future source's constructor forgets to register its credential — the L-001 recurrence
  (guarantee proven in a test but not enforced at the real production seam, class of E-003/E-007)
  now has a production-wiring regression guard. Status: closed.
