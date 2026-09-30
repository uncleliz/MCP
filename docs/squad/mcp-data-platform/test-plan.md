# MCP Data Platform — Test Plan

> Grounded in `requirements.md` (FR-001…FR-015, NFR-001…NFR-005, BR-001…BR-005, **37** AC id —
> the file itself lists 37; the BA's own HANDOFF undercounted this as 36, see
> `implementation-plan.md` "Ghi chú đếm" for the reconciliation. This plan uses 37 as truth),
> `architecture.md` (16 ADR, 29 amendments, spikes S1–S5), `api-contract.yaml` (49 MCP tools +
> 6 `x-interface: cli` operations, all `x-readonly: true` except the CLI mutations on `kb`
> itself), and `implementation-plan.md` (86 tasks T-001…T-086).

## Scope & out of scope

**In scope.** All 9 read-only MCP servers (`mcp-confluence`, `mcp-gitlab`, `mcp-opensearch`,
`mcp-kibana`, `mcp-cloudwatch`, `mcp-kafka`, `mcp-redis`, `mcp-sqs-sns`, `mcp-pgvector`) across
Phase 1/2/3, plus the `mcp-ingest` CLI pipeline and the shared `packages/mcp_common` library.
Confirmed at Gate B: monorepo layout is `packages/*` (not `backend/`) per ADR-0001, and the
Confluence flavor in scope is **Cloud** (so Confluence test fixtures/endpoints target the Cloud
REST API, not Server/DC). This is a **backend-only** feature — no frontend, no UI, no browser
surface. Coverage target: every AC in `requirements.md` ↔ ≥1 TC in `test-cases.md`; every Must
FR (all of FR-001…FR-015 are Must) has ≥1 AC with ≥1 TC. Business rules BR-001…BR-005 are
exercised indirectly through the FR-014/FR-015/FR-012-tagged TCs (read-only enforcement,
citation, metadata preservation) rather than as a separate axis.

**Out of scope for this test plan:**
- Exact numeric pass/fail thresholds for NFR-002 (timeout bound), NFR-003 (citation-rate
  threshold), NFR-004 (freshness bound), and the default retention window for
  `mcp-ingest prune` — all four are still open (Open questions 1/4/5 in `requirements.md`,
  `mcp-ingest prune` has **no implicit default** per `api-contract.yaml`). Test cases that touch
  these are written with a `# THRESHOLD TBD` marker (grep-able, same convention
  `implementation-plan.md` R20 already uses) and an interim assertion using the numbers
  `architecture.md`/ADR-0006 A2 already committed to code (21s < 25s deadline), so the tests are
  not empty placeholders — only their final pass bound is pending PO.
- The exact Kafka client library (`confluent-kafka` vs `kafka-python`, spike S4 / ADR-0009) and
  the exact embedding provider/model (`bge-m3` vs `multilingual-e5-large`, spike S2 / ADR-0010).
  Test cases target the `KafkaReader`/`EmbeddingProvider` **ports**, not a concrete library, so
  they do not need to change once S2/S4 close — see TC-028 and TC-071.
- Whether RBAC/`visibility` filtering is a Phase 3 must-have (ADR-0016 Part 2, Open question 3,
  the "T-067 equivalent" decision). Both branches of that decision have a placeholder TC
  (TC-072 conditional-on-"yes", TC-073 as the currently-assumed-default-if-"no"); neither is
  dropped, but TC-072 will not actually run in `qa-verify` until Gate B answers this.
- Any frontend/browser/UI testing (none exists), load/performance testing beyond the
  timeout-budget correctness check, and live integration against the 5 real remote systems
  (Confluence, GitLab, OpenSearch, Kibana, CloudWatch) if spike S1 (`docs/spikes/S1-reachability.md`)
  reports them unreachable from the dev/CI network — those live cases are marked
  `@pytest.mark.live` and skipped with a reason that **must** be surfaced in the regression
  report (R1), never silently skipped.

## Test levels — tool per level

| Level | Owner | Tool / mechanism |
|---|---|---|
| **Unit** | BE | `pytest` + `pytest-asyncio` + `respx` (HTTP mocking, no network) + `botocore` Stubber (AWS SDKs) + fixture-based mocks for Redis/Kafka clients. Covers `mcp_common` modules (config, stdout-guard logging, error taxonomy, envelope invariants, golden-file render snapshots, http timeout-budget math, readonly allowlist guard, content/redact), per-server `mappers.py`/`read_api.py` bound validation, and the readonly-tool-surface fixture (`assert_readonly_tool_surface`). No Docker, no real credentials. |
| **Integration** | BE | `pytest` against `infra/docker-compose.yml` (Postgres+pgvector, Redis 7 with `mcp_ro` ACL, Kafka KRaft, LocalStack sqs/sns) for the 4 emulatable sources, and `respx` replaying **captured real payloads** (`tests/fixtures/<server>/*.json`) for the 5 non-emulated remote sources (Confluence, GitLab, OpenSearch, Kibana, CloudWatch). A `@pytest.mark.live` subset hits the real remote systems when reachable (gated by spike S1) — not run by default in CI. Covers `mcp-ingest`'s full crawl→redact→chunk→embed→persist pipeline end to end against the compose Postgres. |
| **Contract** | BE produces / QA verifies in `qa-verify` | Each package emits `tools.snapshot.json`; `test_contract.py` diffs it against `api-contract.yaml` (`check-jsonschema` + `openapi-spec-validator`, ADR-0013 — no `spectral`/`redocly` on dev machines). `structuredContent` is validated against `outputSchema` on **all 4 status branches** (`ok`/`empty`/`not_found`/`error`); the text-rendering golden files validate `info.x-text-rendering`. |
| **Component** | N/A | No frontend/UI component layer exists in this backend-only feature. |
| **E2E** | QA, implemented by `ecc:e2e-runner` under `e2e/` | There is no browser/UI, so "E2E" here means spawning the **actual MCP server process(es) over stdio** and driving them with a real JSON-RPC client (the same protocol Claude Desktop/Code uses): `tools/list`, `tools/call`, `prompts/list`, across the real process boundary. Playwright/browser automation does not apply; `ecc:e2e-runner` should use a stdio MCP client harness instead. This is the only level that can correctly assert **FR-014/AC-002** (calling a nonexistent "write" tool name produces an **MCP SDK JSON-RPC protocol error**, *not* an `ErrorEnvelope` — `api-contract.yaml` `info.description` is explicit that this is a protocol-layer assertion, not a contract-schema one) and the three cross-source synthesis Journeys (FR-003/FR-009/FR-013), which need the real tool-calling round trip across ≥2 live server processes to mean anything. Most of this project's real verification load sits in Unit + Integration + Contract, not E2E. |

## Environments & test data

- **Docker Compose** (`infra/docker-compose.yml`): `pgvector/pgvector:pg16`, `redis:7` (+ ACL
  file provisioning a read-only `mcp_ro` user with `+acl|getuser`), `apache/kafka` (KRaft,
  single node), `localstack` (`sqs,sns`). These 4 sources are the only ones with a real,
  disposable local instance.
- **Fixture payloads**: `tests/fixtures/{confluence,gitlab,opensearch,kibana,cloudwatch}/*.json`
  — real captured payloads (per source, per tool) used by `respx` for Confluence, GitLab,
  OpenSearch, Kibana, CloudWatch, which are not locally emulatable. If spike S1 marks a source
  unreachable from the dev/CI network, only the `@pytest.mark.live` subset for that source is
  affected — unit/integration coverage via fixtures still runs in full.
  Confluence fixtures/endpoints target **Cloud** (`/wiki/rest/api/...`), confirmed at Gate B.
- **Eval question sets** (`eval/questions.yaml`): ≥10 questions per phase
  (`expected_sources`/`expected_behavior`), including a deliberate "one source empty" case per
  phase, used for the manual NFR-003 citation-rate review (TC-065) and driving the three
  prompts (`dev_knowledge_lookup`, `incident_investigation`, `semantic_synthesis`) in the E2E
  Journey tests.
- **Recall benchmark set** (`eval/recall_queries.yaml`): 50 sample semantic-search queries used
  by the ADR-0011 A3 recall≥0.95 gate (TC-040/TC-066), run with HNSW vs.
  `SET enable_indexscan=off` brute-force as ground truth.
- **Credential fixtures for negative startup checks**: a deliberately over-scoped GitLab
  PAT/Confluence token/AWS policy/Redis ACL user/Postgres DSN (`mcp_ingest_rw` swapped into
  `MCP_PGVECTOR_DSN`) used to prove the startup credential gate rejects `serve()` (ADR-0003 A1).

## Entry / exit criteria

**Entry (qa-plan → BE/FE build can start; this document itself has no entry blocker):**
`mcp_common` foundation tasks (T-005…T-014) green, `api-contract.yaml` validated, Docker Compose
infra available.

**Exit (for `qa-verify` to report PASS):**
- All P1 test cases pass.
- 0 open Critical/High defects.
- Coverage ≥ 80% on changed code (Definition of Done #2 in `implementation-plan.md`; `mcp_common`
  held to a higher bar as the shared dependency of all 10 packages, R11).
- `structuredContent` validated against `outputSchema` on all 4 status branches for every tool
  exercised.
- NFR-001 (100% of attempted mutating operations rejected) passes with **zero** exceptions across
  all 9 servers.
- The ADR-0011 A3 recall≥0.95 gate passes on the 50-query benchmark.
- `uncovered_ac` remains `[]` (37/37 AC mapped to ≥1 passing or explicitly-triaged TC).
- Any `@pytest.mark.live` test skipped due to unreachable infra (R1) is explicitly listed in the
  regression report with a reason — never silently dropped from the pass/fail count.
- TCs marked `# THRESHOLD TBD` / conditional-on-Gate-B (TC-062, TC-065, TC-067, TC-071, TC-072,
  TC-074) report their measured value/behavior even though their final numeric pass bound is
  pending PO/Gate B; they do not block PASS on the *unset* number, only on the interim assertion
  already committed in ADR-0006 A2 / the contract's explicit "no implicit default" rule.

## Risks

Carried over from `architecture.md` "Risks & spikes" and `implementation-plan.md` "Risks &
mitigations", restated from a QA verification angle:

| Risk | QA impact | Mitigation in this plan |
|---|---|---|
| R1 — VPN/network unreachable to 5 remote sources | Integration/live tests for Confluence, GitLab, OpenSearch, Kibana, CloudWatch may not be runnable from CI | Fixture-based unit/integration tests (respx) always run; `@pytest.mark.live` subset skipped-with-reason, must appear in regression report |
| R2 — stdout pollution breaks the stdio session | A tool leaking one stray byte to stdout silently kills the whole MCP session | Dedicated stdout-guard TC (TC-069) runs against every package's unit suite, not just one |
| R4 — secret persisted into `kb.chunks` and replayed via semantic search | Worse than a transient leak: permanent and queryable | TC-051 asserts secret-looking content never reaches `kb.chunks` nor a search result, and is visible in `kb.ingest_failures` instead |
| R6 — embedding quality unmeasured on real data | Recall/latency numbers in this plan are provisional until spike S2 closes | TC-071 targets the `EmbeddingProvider` port, re-runs unaffected by which of the two 1024d candidates S2 picks |
| R7 — `confluent-kafka` may fail to install | Kafka test suite could be written against the wrong client | TC-028 targets the `KafkaReader` port abstraction, not a concrete library |
| R16 — executor exhaustion silently hangs all subsequent calls | Invisible failure mode: earlier calls still return clean errors while later ones hang forever | Dedicated TC-063 (N+1th call must fail fast, not hang) |
| R17 — Kafka `auto.create.topics.enable=true` turns the FR-007/AC-002 negative test into an actual write | The very test meant to prove read-only could itself create a topic | TC-025 explicitly diffs the topic list before/after the call |
| R18 — silent data loss in ingest checkpoint/reconcile | `status=success` could mask a skipped document; a partial crawl could mass-tombstone the corpus | TC-046 (checkpoint doesn't advance past a failed doc), TC-047 (reconcile safety valve blocks mass-tombstone) |
| R19 — `mcp-pgvector` (read-only) needs the same `EmbeddingProvider` port that lives in `mcp_ingest` (which owns the write-capable `mcp_ingest_rw` role) | A packaging mistake here could smuggle write-path code into a read-only server | Covered by BE's import-linter test (T-058); QA does not duplicate it but TC-043/044 independently prove the pgvector *role* itself can't write regardless of what it imports |
| R20 — NFR-002/003/004 thresholds and `prune` retention default are all still TBD | Verification tests would otherwise have empty/unwritable assertions | Each such TC carries a `# THRESHOLD TBD` marker and an interim assertion (ADR-0006 A2 numbers, or "must be passed explicitly, no default") so the test is real today and only its final numeric bound changes later |
| Gate B decision #5 (ADR-0016 RBAC) still open | A whole task family (T-067) may or may not exist depending on the answer | TC-072 (conditional) and TC-073 (current default) both drafted now; `qa-verify` runs whichever branch Gate B has actually selected by then |
