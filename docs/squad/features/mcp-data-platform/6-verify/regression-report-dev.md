# mcp-data-platform — Regression Report · env dev (run 2 / review re-run R-005, 2026-10-01)

> Closes review finding **R-005** (`6-verify/review-report.md`): the 10 **P1** Postgres+pgvector
> E2E tests created at HEAD `0363fcc` that had **never run on any host** because they need a real
> Postgres+pgvector and Docker was previously down. Docker is now UP on this macOS host, so this
> run executes them for the first time, re-runs the full `e2e/` suite with no Postgres skips, runs
> the ADR-0011 A3 recall gate and the pgvector-specific (`hnsw.iterative_scan`, role-refusal)
> branches against a real pgvector **0.8.6** server, and runs every `@pytest.mark.live` test whose
> upstream can be emulated locally. Run 1 (macOS, no Docker) is preserved at
> `evidence/legacy/regression-report.md`.

## Summary

| Level | Tool | Collected | Passed | Failed | Skipped | Notes |
|---|---|---|---|---|---|---|
| E2E | `pytest e2e/` (stdio MCP client, real server subprocesses) + real Postgres+pgvector | 34 | **34** | 0 | **0** | run 1 was 24 passed / 10 skipped; the 10 skips were exactly the R-005 P1 tests — now all run |
| E2E — the 10 R-005 P1 tests only | `pytest e2e/test_pgvector_ingest_e2e.py` | 10 | **10** | 0 | 0 | TC-045, 049, 051, 052, 053, 067, 068, 073, 074, 077 — first execution ever |
| BE unit + integration | `pytest packages/ --cov` | 2009 | **1845** | 0 | 164 | 164 skips = 153 local-initdb-fixture + 11 `@pytest.mark.live` (6 of which are proven green below) |
| Live (emulatable upstream) | `pytest -m live` against the compose/throwaway stack | 6 | **6** | 0 | 0 | redis ×1, sqs/sns ×1, kafka ×3, pgvector ×1 — see Results |
| Live (real remote upstream) | — | 5 | — | — | **5** | cloudwatch, confluence, gitlab, kibana, opensearch — need real VPN/creds, skip-with-reason (not fabricated) |
| Frontend | — | — | — | — | — | N/A — backend-only feature, no `frontend/` tree exists |

**Aggregate backend coverage this run: 89.97%** (`TOTAL 8990 stmts, 767 miss, 90%`;
`Required test coverage of 80.0% reached. Total coverage: 89.97%`) — clears the ≥ 80%
Definition-of-Done bar. The modules still sitting low are the Postgres-dependent
`mcp_ingest`/`mcp_pgvector` paths whose **package-level** integration tests still skip on this
macOS host (the `packages/conftest.py` `pg_server` fixture is Debian-only, R-005 secondary
finding, owned by backend); those same behaviours are, however, now exercised end-to-end through
the E2E layer against the real pgvector server, so the behaviour is verified even where the
package-level coverage line is not.

### Real environment used (evidence `evidence/qa-dev/20261001-170716-environment.txt`)

- Docker Engine **29.7.2** (host: macOS / Apple silicon, aarch64).
- **Postgres + pgvector for the E2E/recall/pgvector-live runs**: a throw-away
  `pgvector/pgvector:pg16` container (`mcp-qa-r005-pg`) — **PostgreSQL 16.15**, **pgvector 0.8.6**
  (≥ 0.8, satisfies ADR-0011 A3), trust auth, user `postgres`, published on `127.0.0.1:55433`.
  This reproduces exactly the semantics of the `pg_server` fixture (a throw-away trust-auth
  cluster with a fresh database per test, dropped on teardown) but on a pgvector-enabled image —
  wired in via an **`e2e/`-local** `conftest.py` override gated on `MCP_E2E_PG_URL` (QA-owned path,
  `packages/**` untouched).
- **Compose stack** (`infra/docker-compose.yml` + `infra/docker-compose.kafka-autocreate.yml`):
  `redis:7` (6379, healthy), `apache/kafka:3.7.0` strict (9092) + autocreate (9094),
  `localstack/localstack:3` (4566, healthy). The compose `postgres` service could not bind host
  port 5432 (a local EDB PostgreSQL 17 already holds it); it was not needed — the throw-away
  container on 55433 covered every Postgres path.

## E2E audit before running (per the task)

- `git status` at start showed `e2e/test_stdio_readonly_e2e.py` as modified with the run-1 TC-069
  harness fix on disk (removal of the manual `proc.stdin.close()`), as the task described.
- **Between the audit and the first run the working tree was reverted to HEAD `0363fcc`** (the
  uncommitted TC-069 fix was lost; `git diff` went empty and the fixed blob `aef27d6` is absent
  from the object store). The first full-suite run therefore surfaced the TC-069 failure again,
  deterministically, on all three parametrizations (`confluence|kibana|sqs_sns`).
- Because `e2e/**` is QA-owned and TC-069 is a **test-harness** defect (never product code), the
  fix was **re-applied** to `e2e/test_stdio_readonly_e2e.py` (let `communicate()` close stdin
  itself; a manual `stdin.close()` makes CPython 3.12's `communicate()` flush raise
  `ValueError: I/O operation on closed file`). Re-verified green. See Failures triage.
- Only `e2e/**` was touched this run: `e2e/conftest.py` (new `MCP_E2E_PG_URL` fixture override) and
  `e2e/test_stdio_readonly_e2e.py` (re-applied TC-069 fix). `packages/**` was not modified.

## Results

### The 10 R-005 P1 E2E tests — first-ever execution, all PASS

Command: `MCP_E2E_PG_URL=postgresql://postgres@127.0.0.1:55433/postgres pytest e2e/test_pgvector_ingest_e2e.py -v`
(evidence `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt`).

All ten rows share the evidence log `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt`
(and appear again in `…-e2e-full.txt`); the node id is given per row.

| TC | Covers | Status | Evidence |
|---|---|---|---|
| TC-045 | ingest→semantic_search roundtrip + citation to original URL | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_045_ingest_then_semantic_search_roundtrip_with_citation` |
| TC-049 | re-run unchanged/changed does not duplicate (FR-012 AC-003) | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_049_rerun_unchanged_and_changed_does_not_duplicate` |
| TC-051 | secret (`AKIA…`) never stored in `kb.chunks` nor returned | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_051_secret_never_stored_nor_returned` |
| TC-052 | Journey 3 synthesis cites original URLs + SQS queue (moto) | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_052_semantic_synthesis_cites_original_urls_and_queue` |
| TC-053 | off-topic question yields explicit `empty` | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_053_off_topic_question_yields_explicit_empty` |
| TC-067 | `status --json` reports staleness; never-ingested source reported, not error | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_067_status_json_reports_staleness` |
| TC-068 | `kb_list_sources` empty-db then populated | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_068_list_sources_empty_db_then_populated` |
| TC-073 | restricted page rejected + purged on relabel (FR-013 Journey 3) | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_073_restricted_page_rejected_and_purged_on_relabel` |
| TC-074 | `prune` requires explicit retention, dry-run default | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_074_prune_requires_explicit_retention_and_dry_run_is_default` |
| TC-077 | `kb_list_sources` hides a fully tombstoned source | pass | `evidence/qa-dev/20261001-170716-e2e-pgvector-10xP1.txt` · `::test_TC_077_list_sources_hides_all_tombstoned_source` |

`10 passed in 22.04s`. These exercise the real `mcp-ingest` CLI subprocess → real Postgres 16 +
pgvector 0.8.6 → real `mcp-pgvector` stdio server, with the Confluence REST stub and the HTTP
embedding stub (deterministic hashed bag-of-words). FR-013/AC-001 and FR-013/AC-002 (Journey 3,
Must) now have **executed** behavioural coverage, closing the R-005 gap.

### Full `e2e/` suite — 34/34 PASS, 0 skipped

`MCP_E2E_PG_URL=… pytest e2e/ -q` → `34 passed in 86.57s`
(evidence `evidence/qa-dev/20261001-170716-e2e-full.txt`). Run 1's 10 Postgres skips are gone; the
previously-failing `test_TC_069_stdout_carries_only_jsonrpc_frames[confluence|kibana|sqs_sns]` pass
after the harness fix was re-applied.

### ADR-0011 A3 recall gate — PASS on real pgvector 0.8.6

`pytest`-equivalent via `scripts/recall_benchmark.py --provider fake` over the 50-query synthetic
set, synthetic corpus (1320 chunks) loaded through the real ingest pipeline, `ANALYZE` run as
owner before measuring (evidence `evidence/qa-dev/20261001-170716-recall-fake.json`):

```json
{"top_k": 10, "queries": 50, "mean_recall": 1.0, "min_recall": 1.0,
 "hnsw_index_used": true, "threshold": 0.95, "passed": true}
```

- **mean_recall 1.0, min_recall 1.0 ≥ 0.95** — gate passes.
- **`hnsw_index_used: true`** — the measured path really plans the `chunks_embedding_hnsw` index
  (the benchmark forces `enable_seqscan=off` so a small corpus still exercises the ANN index, then
  EXPLAINs the server's actual `search` statement). On the first attempt this read `false` /
  `passed: false` purely because statistics had not been collected yet; after `ANALYZE kb.chunks`
  the planner selects the HNSW index and the gate passes. This closes the run-1 Gate C caveat that
  the pgvector-specific index path had never run on Linux/Docker.

**Honest scope of this number (consistent with review finding R-004):** with `--provider fake`
this measures **ANN-index recall** — whether HNSW returns the same rows as exact brute-force —
**not semantic relevance**. The corpus and queries share a seed, so it says nothing about retrieval
quality for real questions. The real-model (`--provider configured`) run remains **blocked** by the
ADR-0010 model decision, which is blocked by Hugging Face egress; NFR-003 semantic quality stays
**unverified**. R-004 (owner sa+qa) governs correcting the ADR-0011 A3 / `signoff/phase-3.md`
wording — this report does not re-assert semantic recall, only the ANN-index gate on real 0.8.6.

### pgvector-specific branches on real Docker/Linux pgvector — PASS

- `mcp-pgvector doctor` against the `mcp_query_ro` role on the real server:
  `credentials + read-only check: ok · role: mcp_query_ro · pgvector: 0.8.6` — confirms the server
  takes the **≥ 0.8 `hnsw.iterative_scan`** branch (not the over-fetch fallback).
- Live `test_live_iterative_scan_filtered_search_and_role_refusal` (`pytest packages/mcp_pgvector
  -m live` against the real server, `MCP_LIVE_PG_ADMIN_DSN` → the throw-away container) **passes**:
  asserts `supports_iterative_scan` true, filtered search returns `ok`/explained-`empty`
  (ADR-0011 A3 false-negative guard), and the write role `mcp_ingest_rw` is **refused** for serving
  (`INSERT on kb.chunks` detected → read-only guarantee holds under password auth).

### `@pytest.mark.live` suite (evidence `evidence/qa-dev/20261001-170716-live-emulatable.txt`)

Run with `MCP_LIVE_TESTS=1` against the running stack:

| Live test | Upstream | Status |
|---|---|---|
| `mcp_redis::test_live_acl_startup_check_and_read_paths` | compose redis `mcp_ro` ACL | pass |
| `mcp_sqs_sns::test_live_metadata_reads_leave_message_counts_unchanged` | LocalStack sqs/sns | pass |
| `mcp_kafka::test_live_describe_and_peek_cite_topic_partition_and_string_offset` | compose kafka 9092 | pass |
| `mcp_kafka::test_live_R17_describing_a_missing_topic_on_an_autocreate_broker_creates_nothing` | kafka autocreate 9094 | pass |
| `mcp_kafka::test_live_peek_and_describe_group_do_not_move_another_groups_committed_offsets` | compose kafka 9092 | pass |
| `mcp_pgvector::test_live_iterative_scan_filtered_search_and_role_refusal` | throw-away pgvector 0.8.6 | pass |

**Skipped `@live`, with reason, not fabricated** (need a real remote system + VPN/credentials, none
available on this host): `mcp_cloudwatch`, `mcp_confluence`, `mcp_gitlab`, `mcp_kibana`,
`mcp_opensearch` startup/search integration tests (5). Reason recorded by the suite:
`"live integration test: needs real credentials/VPN or the Docker compose stack"`.

### BE suite (evidence `evidence/qa-dev/20261001-170716-backend-cov.txt`)

`pytest packages/ --cov` → `1845 passed, 164 skipped`, 0 failed, 0 errors. Coverage 89.97% (≥ 80%).
The 164 skips: **153** × local-initdb `pg_server` fixture (Debian-only; macOS host — R-005
secondary finding, owner backend) + **11** × `@pytest.mark.live` (of which 6 are proven green above
when `MCP_LIVE_TESTS=1` + stack is set; the other 5 are the remote-only ones). No `FAILED`/`ERROR`
lines. NFR-001 (every attempted mutating operation rejected across all 9 servers) remains confirmed
by TC-056 (9/9 server parametrizations) in the full e2e run.

## Failures triage

No open failing test cases this run (0 failed, 0 errors at every level). One carried-over,
already-resolved harness defect is recorded for the audit trail:

| TC | Symptom | Suspected owner | Class |
|---|---|---|---|
| TC-069 | `ValueError: I/O operation on closed file` from `subprocess.communicate()` — deterministic on CPython 3.12, 3/3 parametrizations; the run-1 fix had been reverted to HEAD on disk, re-applied here in `e2e/test_stdio_readonly_e2e.py`, re-verified green | QA (`e2e/` harness) | test-flaky |

Class `test-flaky` is used in the sense required by the triage schema (a defect in a test itself,
QA-owned, no product/contract defect); the failure was deterministic, not intermittent, and was
fixed rather than deferred. Not opened as an `E-` ledger entry: it is a re-application of a
previously-recorded run-1 harness fix, not a new product failure escaping a stage.

## Verdict: PASS

All 10 R-005 P1 E2E tests execute for the first time and pass; the full `e2e/` suite is 34/34 with
zero Postgres skips; the ADR-0011 A3 recall gate passes (mean 1.0, `hnsw_index_used: true`) and the
pgvector ≥ 0.8 `iterative_scan` + read-only-role-refusal branches are confirmed on a real
pgvector 0.8.6 server; the 6 emulatable `@live` tests pass; backend coverage 89.97% clears ≥ 80%.
All remaining skips are reasoned and traced to a single environment cause (the Debian-only
package-level `pg_server` fixture on a macOS host, and the 5 remote-only `@live` upstreams needing
VPN/credentials), not to any backend or contract defect.

**R-005 is closed** by this run. Two caveats carry forward (neither blocks closing R-005):

1. **R-004 (not R-005):** the recall number is ANN-index recall under the fake provider, not
   semantic relevance; NFR-003 semantic quality stays unverified until the ADR-0010 model is chosen
   and a `--provider configured` run is possible (HF egress blocked). Owner sa+qa, per the review.
2. **R-005 secondary (owner backend):** `packages/conftest.py::_find_pg_bin()` only probes the
   Debian path `/usr/lib/postgresql/*/bin/initdb`, so the **package-level** Postgres integration
   tests still skip on non-Debian hosts. The E2E layer now covers those behaviours against real
   pgvector via the `e2e/`-local `MCP_E2E_PG_URL` override, but making the package fixture portable
   (read `PATH` / honour an env override) is a backend change outside QA's write scope.
