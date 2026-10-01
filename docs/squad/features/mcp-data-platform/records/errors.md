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
