# MCP Data Platform — Regression Report (run 1, 2026-10-01)

> Resumes the qa-verify run that was stopped mid-way for lack of cloud credit (see
> `state.json` history and `SESSION-HANDOFF.md`). `e2e/` and the TC-076 wording fix in
> `test-cases.md` already existed on disk; this run triages the one carried-over failure,
> finishes the full E2E pass, runs the BE suite with coverage, and closes out run 1.

## Summary

| Level | Tool | Collected | Passed | Failed | Skipped | Notes |
|---|---|---|---|---|---|---|
| E2E | `pytest e2e` (stdio MCP client, real server subprocesses) | 34 | 24 | 0 | 10 | All 10 skips are the local-Postgres fixture, skip-with-reason (see below) |
| Unit + Integration (BE) | `pytest packages/` with `--cov` | 1986 | 1822 | 0 | 164 | 164 skips = 98 local-Postgres-fixture + 66 `@pytest.mark.live` (need VPN/creds/Docker) |
| Frontend | — | — | — | — | — | N/A — backend-only feature, frontend stage skipped at Gate B/lead (confirmed in `state.json`) |

**Aggregate coverage (this machine, this run): 90.27%** (`TOTAL 8881 stmts, 733 miss, 90%` —
`Required test coverage of 80.0% reached. Total coverage: 90.27%`), clearing the
Definition-of-Done ≥ 80% bar. Per-module coverage for `mcp_common` ranges 87–100%, comfortably
above its "higher bar" (R11); the modules sitting lowest overall are the Postgres-dependent
`mcp_ingest` command/pipeline modules and `mcp_pgvector/client.py` (21–66%), which is a direct
consequence of this machine skipping every Postgres-backed test, not a coverage regression — the
backend's own last full run (state.json, Phase 3b, run on a Linux dev container with the distro
Postgres binaries present) reported **95.9%** with those same tests executing. Re-running
`make ci` on that Linux environment (or with `infra/docker-compose.yml` up) before Gate C would
restore the higher number; this is already flagged in `SESSION-HANDOFF.md`'s Gate C checklist
and is not a new finding.

0 failures, 0 errors, across both levels combined (1822 + 24 = 1846 passed; 0 failed).

## TC-069 triage (carried over from the interrupted run)

**Root cause confirmed: test-harness bug, not a backend defect.**

`e2e/test_stdio_readonly_e2e.py::test_TC_069_stdout_carries_only_jsonrpc_frames` drove the server
subprocess by hand (not through the MCP client), then in its `finally` block called
`proc.stdin.close()` and immediately afterward `proc.communicate(timeout=30)`. On Python 3.12.6
(the version in this project's `.venv`), `subprocess.Popen._communicate()` unconditionally calls
`self.stdin.flush()` before checking whether there is anything left to write, and only swallows
`BrokenPipeError` there — not `ValueError`. Flushing an already-closed file object raises exactly
`ValueError: I/O operation on closed file`, which is what the interrupted run observed. (CPython
3.13+ added a guard that swallows this specific `ValueError` when the stream is already closed —
confirmed by diffing `inspect.getsource(subprocess.Popen._communicate)` between the system
Python 3.14.3 and the project's venv Python 3.12.6 — but this project pins/uses 3.12.6, so the
test must not rely on that newer behavior.)

Fix applied in `e2e/test_stdio_readonly_e2e.py`: removed the test's own `proc.stdin.close()` call
and let `proc.communicate()` do the flush-then-close itself (it already does `if not input:
self.stdin.close()` right after the flush, since the test passes no `input=` to `communicate()`).
This keeps the test's intent fully intact — it still proves every line read from the subprocess's
real stdout is a well-formed JSON-RPC frame, and that stderr carries the structured logs — only
the stdin teardown sequencing changed.

Verified: `pytest e2e/test_stdio_readonly_e2e.py -v` → 15/15 pass, including all 3
`test_TC_069_stdout_carries_only_jsonrpc_frames[confluence|kibana|sqs_sns]` parametrizations.
Re-confirmed independently by the `ecc:e2e-runner` agent's own full-suite run (see Results below).

Classification: **test-flaky** is not the right label (the failure was 100% deterministic given
Python 3.12, not intermittent) — this is a harness defect, filed here as a fix rather than a
triage-and-defer item since it was within QA's remit (`e2e/`) to correct and did not touch
product code.

## Results

### E2E (`pytest e2e -v`, no `-x`, full run — both my own run and the independent `ecc:e2e-runner` run agree)

All 8 official `Level = E2E` test-cases.md rows pass, plus additional TC ids implemented at the
E2E layer beyond their nominal Integration/Unit tag (extra depth, not a gap):

| TC | Status | Evidence |
|---|---|---|
| TC-005 | PASS | `e2e/test_stdio_readonly_e2e.py::test_TC_005_confluence_unknown_write_tool_refused` |
| TC-014 | PASS | `e2e/test_journeys_e2e.py::test_TC_014_dev_knowledge_lookup_cites_both_sources` |
| TC-015 | PASS | `e2e/test_journeys_e2e.py::test_TC_015_empty_confluence_is_stated_not_fabricated` |
| TC-018 (×4) | PASS | `e2e/test_opensearch_dsl_e2e.py::test_TC_018_forbidden_constructs_not_permitted_without_upstream_call[construct0..3]` |
| TC-033 | PASS | `e2e/test_journeys_e2e.py::test_TC_033_incident_investigation_cites_each_source` |
| TC-034 | PASS | `e2e/test_journeys_e2e.py::test_TC_034_empty_cloudwatch_alarms_stated_as_gap` |
| TC-045 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_045_ingest_then_semantic_search_roundtrip_with_citation` |
| TC-049 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_049_rerun_unchanged_and_changed_does_not_duplicate` |
| TC-051 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_051_secret_never_stored_nor_returned` |
| TC-052 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_052_semantic_synthesis_cites_original_urls_and_queue` |
| TC-053 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_053_off_topic_question_yields_explicit_empty` |
| TC-054 (×2) | PASS | `e2e/test_stdio_readonly_e2e.py::test_TC_054_live_tool_surface_is_48_and_readonly`, `test_TC_054_opensearch_dsl_toggle_adds_exactly_one_tool` |
| TC-056 (×9 servers) | PASS | `e2e/test_stdio_readonly_e2e.py::test_TC_056_unknown_write_tool_refused_on_every_server[*]` |
| TC-067 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_067_status_json_reports_staleness` |
| TC-068 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_068_list_sources_empty_db_then_populated` |
| TC-069 (×3) | PASS (fixed, see above) | `e2e/test_stdio_readonly_e2e.py::test_TC_069_stdout_carries_only_jsonrpc_frames[confluence\|kibana\|sqs_sns]` |
| TC-073 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_073_restricted_page_rejected_and_purged_on_relabel` |
| TC-074 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_074_prune_requires_explicit_retention_and_dry_run_is_default` |
| TC-076 | PASS | `e2e/test_opensearch_dsl_e2e.py::test_TC_076_dsl_size_clamp_and_paging_bounds` |
| TC-077 | SKIP (local Postgres fixture) | `e2e/test_pgvector_ingest_e2e.py::test_TC_077_list_sources_hides_all_tombstoned_source` |

All 10 skips carry the identical reason: `"no local PostgreSQL server binaries (initdb) found;
use the live tests"` — raised by `packages/conftest.py`'s `pg_server` fixture, which only probes
the Linux distro path `/usr/lib/postgresql/*/bin/initdb` by design (its own docstring: meant for
CI/dev-container Linux boxes; the compose Postgres under `infra/docker-compose.yml` is the
intended target of the separate `@pytest.mark.live` tests). This dev machine is macOS with
PostgreSQL installed at `/Library/PostgreSQL/17/bin` (a different layout) and no Docker daemon
running, so the fixture skips every test that needs it, exactly as designed — not a silent drop,
not a new gap (R1 in `test-plan.md` anticipates this class of skip and requires it be surfaced
with a reason, which this report does). None of these 10 are backend defects; they are
unexercised on this machine only. They were previously exercised and green on a Linux dev
container (state.json "Phase 3b … done" entry: 1975 passed, 11 live skipped, coverage 95.9%).

Per TC-covers-AC traceability in `test-cases.md`, no AC lost coverage because of these skips:
each skipped TC still has non-E2E coverage (Integration-level `pytest.mark` tests in
`packages/mcp_pgvector` and `packages/mcp_ingest`, which hit the identical Postgres-fixture skip
on this machine for the same reason — see BE results below) plus the TC itself remains a real,
executable test, just not executed *here*.

### BE unit + integration (`uv run pytest --cov=... packages/`)

```
1822 passed, 164 skipped in 128.98s
TOTAL 8881 stmts, 733 miss, 2140 branch, 167 partial, 90% cover
Required test coverage of 80.0% reached. Total coverage: 90.27%
```

0 failed, 0 errors. The 164 skips split as:
- **98** × `"no local PostgreSQL server binaries (initdb) found; use the live tests"` — same
  fixture/cause as the E2E skips above, across `mcp_ingest` (checkpoint, cli_contract, integration,
  migrations, persist_idempotent, prune, reconcile_valve, reembed, retry_failed, scheduler,
  stage_redact, status) and `mcp_pgvector` (contract, db_integration, recall, settings_cli,
  timeout_budget).
- **66** × `"live integration test: needs real credentials/VPN or the Docker compose stack (set
  MCP_LIVE_TESTS=1 to run)"` — the `@pytest.mark.live` tests for Kafka, Kibana, OpenSearch,
  Redis, SQS/SNS, pgvector integration — expected per R1, this machine has no VPN/creds/Docker.

No `FAILED` or unexpected `ERROR` lines anywhere in the run. NFR-001 (100% of attempted mutating
operations rejected, zero exceptions across all 9 servers) is directly confirmed by TC-056
passing for all 9 server parametrizations with no server excluded.

Exit-criteria items this run could **not** independently re-confirm on this machine (all because
of the same missing-local-Postgres/Docker constraint, not because of a defect):
- The ADR-0011 A3 recall ≥ 0.95 gate (`mcp_pgvector/tests/test_recall.py`) — skipped here.
- `structuredContent` vs `outputSchema` contract validation for the pgvector-specific branches
  in `mcp_pgvector/tests/test_contract.py` (5 of its cases skipped here; the other 8 packages'
  contract suites ran in full and passed).

These were exercised and green in the backend's own prior Linux-container run (state.json:
"Phase 3a … done … 1779 passed … make ci green"; "Phase 3b … done … 1975 passed … coverage
95.9%"). Recommend re-running `make ci` (or at minimum `pytest packages/mcp_pgvector
packages/mcp_ingest`) on a Linux box or with `infra/docker-compose.yml` up, as already queued in
`SESSION-HANDOFF.md`'s Gate C checklist, before treating the recall gate and full pgvector
contract suite as re-verified for Gate C sign-off. This is carried forward as a Gate C
precondition, not a qa-verify failure.

## Failures triage

No *open* failing test cases — 0 failed, 0 errors across both E2E and BE levels in this run.
One failure was carried over from the interrupted prior run and is recorded here for the
record, already resolved before this report:

| TC | Symptom | Suspected owner | Class |
|---|---|---|---|
| TC-069 | `ValueError: I/O operation on closed file` in `subprocess.communicate()` — deterministic on Python 3.12, reproduced 100% before the fix; now fixed in `e2e/test_stdio_readonly_e2e.py` and re-verified green | QA (`e2e/` harness) | test-flaky (closed — was a harness bug, not an intermittent/non-deterministic failure; no backend or contract defect involved) |

## Verdict: PASS

All P1 test cases that were executable on this machine pass (0 failures, 0 errors). The only
TC-069 failure carried over from the interrupted run was a test-harness bug, triaged and fixed
without touching product code, and re-verified green (both directly and via an independent
`ecc:e2e-runner` run). All skips are explicitly reasoned, expected, and traced to a single known
environment cause (no Docker / no Linux-path local Postgres on this dev machine) rather than to
any backend or contract defect — consistent with `test-plan.md`'s R1 handling. Aggregate coverage
(90.27%) clears the ≥ 80% Definition-of-Done bar on this run; full-environment coverage (95.9%,
previously measured) and the recall≥0.95 / full pgvector-contract gates should be re-confirmed on
a Docker/Linux-Postgres-capable environment before Gate C, per the pre-existing
`SESSION-HANDOFF.md` checklist — this is a Gate C precondition already tracked, not a new
blocking finding from this qa-verify run.
