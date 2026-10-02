# MCP Data Platform — Test Plan

> Grounded in `requirements.md` (FR-001…FR-015, NFR-001…NFR-005, BR-001…BR-005, **37** AC id —
> the file itself lists 37; the BA's own HANDOFF undercounted this as 36, see
> `implementation-plan.md` "Ghi chú đếm" for the reconciliation. This plan uses 37 as truth),
> `architecture.md` (16 ADR, 29 amendments, spikes S1–S5), `api-contract.yaml` (49 MCP tools — 48 registered by default, 49 with `MCP_OPENSEARCH_ALLOW_DSL=true` — +
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
  (TC-072 conditional-on-"yes", TC-073 as the default-if-"no"). **Resolved:** ADR-0016 A1
  chose a team-only corpus and T-067 is closed, so TC-072 is closed as not applicable (not run);
  TC-073 is the active case and also covers purge-on-relabel (team→restricted removes chunks and
  tombstones the document).
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
- TCs marked `# THRESHOLD TBD` / conditional-on-Gate-B (TC-062, TC-065, TC-067, TC-071,
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
| Gate B decision #5 (ADR-0016 RBAC) — **resolved: team-only**, T-067 closed | Residual: Confluence restriction changed without content change stays searchable until next full reconcile (ADR-0016 A3) | TC-072 closed (N/A); TC-073 asserts reject + purge-on-relabel |

---

# CHG-001 Option C + B4 Grounding — Company Knowledge tier (test-plan extension)

> Grounded in `requirements.md` FR-016…FR-022 (**26 new AC id**, NFR-006…NFR-012),
> `api-contract.yaml` (13 new tools — 8 Knowledge + 5 Jira — + the `GroundedResultBase` envelope and
> `status=insufficient_evidence`), `architecture.md` (Company Knowledge tier, epics E1…E8, two choke
> points), `implementation-plan.md` (T-087…T-110 + qa_notes), ADR-0018 (B4 grounding contract, GT-1..GT-7),
> ADR-0016 (document visibility, team-only default-deny), ADR-0020/0021/0022, and lessons **L-001**
> (adversarial test for a safety guarantee at a single structural choke point) and **L-002** (never let a
> metric carry two meanings; mark the unmeasured NFR UNVERIFIED instead of inventing a number).
> **The base feature (TC-001…TC-077, FR-001…FR-015) is live and unchanged**; this extension adds TC-078…TC-110.

## Scope & out of scope — CHG-001

**In scope.** The 2 new read-only MCP servers (`mcp-knowledge` 8 tools, `mcp-jira` 5 tools), the in-process
`mcp_gateway` boundary, the hybrid-RAG + local-offline rerank + context-compression pipeline, the **B4
grounding gate** (context-pack assembler, choke point #2), the **permission choke point #1**
(`enforce_permission()` default-deny before context assembly), the Jira connector (`source_type='jira'`),
and the `kb` schema extensions (0007/0007b/0008 — `document_versions`, `entities`, `relationships`,
`knowledge_summaries`, `document_permissions`, source-authority config). Backend-only; no frontend/UI/browser.
Coverage target: every new AC (FR-016…FR-022) ↔ ≥1 new TC; every GT-1..GT-7 of ADR-0018 ↔ a named TC; the
read-only + stdio + no-egress + permission-before-grounding invariants carried as regression TCs on the new
surface (TC-081, TC-084, TC-091, TC-103, TC-104, TC-105).

**Out of scope for this extension.**
- **Numeric FACT↔LOW_CONFIDENCE thresholds `τ_fact`/`τ_low` and real grounding recall** — TBD/UNVERIFIED,
  blocked by HuggingFace egress (NFR-010, NFR-003; D-002 ĐK1 residual carried to CAB). TCs that touch these
  (TC-088, TC-099, TC-106) carry `# THRESHOLD TBD`, assert only verdict-direction invariants +
  `calibration_status: uncalibrated`, and never invent a number (L-002/E-004). The no-evidence⇒UNKNOWN /
  source⇒FACT / conflict⇒CONFLICT invariants (GT-1..GT-4) are **independent of τ and run now**.
- **CHG-002 blocks B2/B5/B6/B7(full)/B8/B9/B10 and Orchestrator** — explicitly not built (backlog); only B4
  Grounding (CTO D-004 in-scope) is exercised here.
- **Per-user RBAC** — ADR-0016 A1 chose a **team-only** corpus; the new surface inherits default-deny, not
  per-user filtering. Permission testing here is "restricted doc never leaks to an un-granted caller", not
  multi-tenant RBAC.
- **HTTP+SSE / multi-user remote hosting of the gateway** — v1.1; the plan only asserts the gateway opens no
  network port today (TC-103).

## Test levels — CHG-001 (same tooling as the base, extended)

| Level | Owner | Tool / mechanism (CHG-001 additions) |
|---|---|---|
| **Unit** | BE | As base + grounding-envelope invariants (`assert_grounding_invariants()` alongside GT-1..GT-7), RRF-k=60 determinism, bounded recursive-CTE termination, the **structural single-choke-point** tests for permission (#1) and grounding gate (#2), the read-only tool-surface for the 2 new servers (61/62 count). |
| **Integration** | BE | `pytest` against compose Postgres (now carrying the 0007/0007b/0008 schema) for hybrid retrieval, grounding verdicts, permission default-deny, versioning/CTE, live-vs-knowledge; `respx` replaying **both** Jira flavors (Cloud `/rest/api/3`, Server/DC `/rest/api/2`); a **socket-assertion / no-egress** harness (TC-081) proving zero outbound sockets during retrieve/rerank/grounding with `HF_HUB_OFFLINE=1`; reranker loaded from local disk. |
| **Contract** | BE produces / QA verifies | `mcp-knowledge`/`mcp-jira` emit `tools.snapshot.json`; diffed against the 13 new operations; `GroundedResultBase` validated against `outputSchema` on all **5** status branches (`ok`/`empty`/`not_found`/`partial`/`insufficient_evidence`); `x-readonly: true`/`x-side-effects: none` asserted on all 13. |
| **E2E** | QA (stdio MCP client, via `ecc:e2e-runner`) | Spawn `mcp-knowledge` and `mcp-jira` over real stdio and drive `tools/list`/`tools/call`; the **FR-014/AC-002 unknown-write-tool JSON-RPC** assertion for both new servers (TC-084, TC-105); the `company_knowledge_lookup` prompt round trip. |

## Environments & test data — CHG-001

- **Compose Postgres** must be migrated to **0008** via the fixed migration-locking runner (T-087/T-090) —
  the schema now has `document_versions`, `entities`, `relationships`, `knowledge_summaries`,
  `document_permissions`, GIN `tsvector` on `kb.chunks`, and seeded `source_authority` + confidence weights
  (`0.5/0.3/0.2`) + `freshness_horizon`. No new datastore/extension (EB-004).
- **Reranker weights** (`bge-reranker-v2-m3`) loaded from **local disk** with `HF_HUB_OFFLINE=1`. A deliberate
  **weights-absent** fixture drives the RRF-only fallback transparency test (TC-080).
- **Permission fixtures** — a `restricted`, un-granted document whose content matches a crafted adversarial
  query (TC-090); grant/visibility rows for the permitted-path test (TC-089).
- **Grounding fixtures** — a no-evidence proposition (GT-1/TC-095), a single-source proposition with a
  resolvable `document_id+chunk_id` (GT-2/TC-096), a two-source conflicting claim (GT-4/TC-097), a
  provenance-missing claim (GT-3/TC-098).
- **Jira fixtures** — captured payloads for **both** Cloud and Server/DC flavors; `@pytest.mark.live` subset
  skipped-with-reason (recorded in the regression report) if Jira is unreachable, exactly as the base's R1
  rule for the 5 remote sources.
- **Grounding eval harness** (`eval/grounding/`) — runs on a deterministic fake provider until HF egress is
  cleared; the real golden-set run (recall + τ calibration) is **conditional** (TC-106).

## Entry / exit criteria — CHG-001

**Entry (qa-plan → build; this document has no entry blocker):** E1 schema path green on the fixed
migration-locking runner (T-087 before any 0007+ migration), `api-contract.yaml` CHG-001 section validated,
compose infra at 0008.

**Exit (for qa-verify to report PASS on the CHG-001 slice):**
- All P1 new TCs pass — including every **GT-1..GT-7** (TC-095..TC-100) and the **permission adversarial /
  structural** TCs (TC-090, TC-091).
- **0 open Critical/High defects**; coverage ≥ 80% on changed code.
- Read-only holds on all **13** new tools (0 write tools; 61/62 surface count asserts; unknown-write-tool
  returns a JSON-RPC protocol error on both new servers — TC-084, TC-104, TC-105).
- **No-egress / stdio** proven: `HF_HUB_OFFLINE=1`, 0 outbound sockets during grounding/rerank (TC-081), the
  gateway opens 0 network ports and logs only to stderr (TC-103).
- **Permission before grounding**: choke point #1 runs before choke point #2, each is a single structural
  gate, and the restricted-doc adversarial test shows 0 leak into candidate/pack/citation.
- `GroundedResultBase` validates on all 5 status branches; the base 9-source envelope does **not** regress.
- `new_uncovered_ac` remains `[]` (26/26 new AC mapped to ≥1 passing or explicitly-conditional TC).
- TCs marked `# THRESHOLD TBD` (TC-088, TC-099, TC-106) report their measured value/direction and assert
  `calibration_status: uncalibrated`; they do **not** block PASS on an unset τ/recall number, only on the
  direction invariants already enforceable today.
- Any `@pytest.mark.live` Jira test skipped due to unreachable infra is listed with a reason in the
  regression report — never silently dropped.

## Risk-based focus — CHG-001 (the riskiest areas and the extra TCs that guard them)

| Risk area (money/auth/data-loss/safety) | Why riskiest | Guard TCs |
|---|---|---|
| **Permission leak** — a `restricted` doc becoming evidence for an un-granted caller | Data-confidentiality breach; the exact class L-001 was written for; team-only corpus's only guarantee | **TC-090** (adversarial, restricted never a candidate/pack/citation), **TC-091** (single choke point #1, before the grounding gate) |
| **Fabrication** — a no-source/weak claim presented as `FACT` | The core CEO guarantee "no fact without an official source"; enforced server-side, not by Claude | **TC-095** (no-source→UNKNOWN+fixed message), **TC-096** (source→FACT, evidence resolves), **TC-098** (missing provenance never FACT), **TC-099** (confidence can't rescue a no-evidence claim) |
| **Conflict hidden** — two sources silently merged / one picked | Misleads the user about contested facts | **TC-097** (CONFLICT exposes all positions + authority_note) |
| **Grounding bypass** — a claim reaching output without the gate | Defeats the whole contract; GT-5 structural | **TC-100** (single gate + GT-7 provenance preserved) |
| **Egress / vendor creep** — an outbound call during rerank/embed/grounding | Breaks vendors=none/no-egress (BR-011); exfiltration risk | **TC-081** (0 outbound socket, `HF_HUB_OFFLINE=1`), **TC-080** (RRF-only fallback transparency) |
| **Write on the new surface** — any of the 13 tools or Jira mutating a source | Breaks the absolute read-only invariant on source #10 + Knowledge tier | **TC-084** (Jira create/transition/comment rejected), **TC-104** (0 write tools, 61/62 count), **TC-105** (JSON-RPC unknown-tool for both servers) |
| **Migration on a populated prod DB** (DK2) | 0007+ runs on the live go-live corpus; a full-table lock would drop read traffic | Verified at build (T-087/T-090); QA re-checks lock-time at UAT/PRE env-promotion, not a unit TC |
| **Gateway opens a network port** | Breaks stdio/NFR-005; new attack surface | **TC-103** (0 listening sockets, stderr-only audit) |
| **Runaway graph traversal** | A cyclic/high-fan-out relationship graph could hang the server | **TC-094** (bounded depth ≤3 + cycle-detect + fan-out LIMIT, terminates) |

## Non-functional tests — CHG-001

| NFR | Method / tool | Threshold | Env |
|---|---|---|---|
| NFR-006 (read-only, new surface) | tool-surface snapshot + transport GET/HEAD + unknown-write-tool JSON-RPC | **100%** mutating ops rejected; 0 write tools on 13 | dev + E2E (TC-081, TC-084, TC-104, TC-105) |
| NFR-007 (permission default-deny correctness) | adversarial permission suite | **100%** of adversarial cases excluded before assembly (per-query latency budget **TBD** — needs the offline reranker measurable, carried with NFR-010) | dev (TC-090, TC-091) |
| NFR-008 (grounding gate singularity) | structural single-choke-point test | exactly **1** gate; **0** bypass paths | dev (TC-091 for #1, TC-100 for #2) |
| NFR-009 (no-fabrication, adversarial) | GT-1..GT-4/GT-6 contract tests | **0** no-evidence claims as FACT; **100%** no-source→UNKNOWN+fixed message; **100%** conflict→CONFLICT | dev (TC-095..TC-099) — **runs now, independent of τ** |
| NFR-010 (confidence calibration) | eval harness on golden-set | **UNVERIFIED** — τ_fact/τ_low TBD; `calibration_status: uncalibrated` until HF egress cleared | conditional (TC-088, TC-099, TC-106) `# THRESHOLD TBD` |
| NFR-011 (deterministic / no egress) | socket assertion during grounding + `HF_HUB_OFFLINE=1` config assertion + dependency/vendor audit | **0** outbound sockets; vendors=none | dev (TC-080, TC-081, TC-103) |
| NFR-012 (stdio kept — no new port) | process/port inspection + stdout guard | **0** listening sockets; audit/log stderr-only | dev (TC-103) |
| NFR-003 (grounding recall) | golden-set recall measurement | **UNVERIFIED** — blocked by HF egress; reports measured value only, no invented bound | conditional (TC-106) `# THRESHOLD TBD` |

## Security smoke — CHG-001

- **AuthZ (another caller's resource):** the restricted-doc adversarial test (TC-090) is the authZ smoke — an
  un-granted caller must not retrieve/rank/cite a restricted document, enforced at choke point #1 before
  retrieval ranking (default-deny; vacuously-granted = denied).
- **Input validation:** JQL is bounded (not arbitrary); oversize/limit caps on the new tools; `max_depth ≤ 3`
  on `find_related_knowledge`; grounding gate rejects claims missing required provenance fields (TC-098).
- **Secrets in responses/logs:** Jira ingest + Knowledge content go through the same redaction + deny-glob as
  the base (ADR-0015); gateway audit redacts and logs queries only at DEBUG; no secret in any `authority_note`
  or provenance field (carried from base R4 regression TC-051; QA spot-checks on the new surface).

## Risks — CHG-001 (QA verification angle)

| Risk | QA impact | Mitigation in this plan |
|---|---|---|
| R-C1 — migration 0007+ locks the populated prod DB (DK2) | A full-table lock drops live read traffic during go-live | Fixed in T-087 before any 0007+ migration; QA verifies lock-time stays under `lock_timeout` at the UAT/PRE promotion gate, not as a unit TC |
| R-C2 — two grounding gates run in parallel (temp fallback not removed) | A claim could leave via an ungated path | TC-100 (GT-5) asserts exactly one gate; the plan builds the assembler before the gate so the ADR-0018 §3 fallback is never used |
| R-C3 — restricted doc leaks into the context-pack | Confidentiality breach | TC-090 adversarial + TC-091 structural; default-deny before retrieval ranking |
| R-C4 — reranker weights missing → silent quality drop | A degraded ranking presented as full-quality | TC-080 asserts RRF-only fallback + `grounding_summary.reranker == "disabled"` (L-002) |
| R-C5 — egress during rerank/embed/grounding | Breaks vendors=none/no-egress; exfiltration | TC-081 (0 outbound socket, `HF_HUB_OFFLINE=1`), TC-103 (gateway no port) |
| R-C6 — a τ number invented and read as proof | Repeats E-004/L-002 | TC-088/TC-099/TC-106 carry `# THRESHOLD TBD`, assert `uncalibrated` + direction invariants only, never a value |
| R-C9 — Jira Cloud vs Server/DC endpoint/cursor divergence | A flavor-specific bug slips through | TC-082 runs the happy path against **both** flavors; the cursor split is opaque in `client.py` |
| R1 (carried) — Jira unreachable from CI/VPN | Live Jira integration may not run | `@pytest.mark.live`; skipped-with-reason must appear in the regression report, never silently dropped |

---

# CHG-003 · Real ingestion + real egress (CLI-driven, 9-source runbook, Confluence first) — test-plan extension

> Grounded in `plan-approval.md` CHG-003 § (Option B, lean track; CEO Gate 1 2026-10-02; CTO sizing D-006),
> `docs/adr/0023-chg003-egress-ingestion-deviation.md` (*proposed*; §6a–§6d decision, **§6e the four
> adversarial/invariant tests**, §7 honest NFR-003 labelling), `requirements.md` FR-023…FR-027 (**17 new AC**),
> NFR-013 (egress default-deny guarantee) / NFR-014 (read-only credential guarantee + honest NFR-003
> enablement), and `implementation-plan.md` CHG-003 (T-111…T-123, the "ADR-0023 §6e → task map" and the
> "Ghi chú cho qa-plan"). Lessons applied: **L-001** (enforce a safety guarantee at a single structural
> choke point + feed it the adversarial input it claims to stop), **L-002** (never read an enabling step as
> proof; mark the unmeasured NFR UNVERIFIED and never invent a number), **L-003** (resolve relocatable
> artifacts path-tolerantly). Recurring security defect classes this change must not repeat: **E-003**
> (token-leak class) and **E-007** (permission choke-point class).
> **The base feature (TC-001…TC-077) and CHG-001 (TC-078…TC-110) are live and unchanged**; this extension
> **appends TC-111…TC-131** — no renumber.

## Scope & out of scope — CHG-003

**In scope.** The newly-relaxed egress/vendor boundary and the real-ingestion CLI path, specifically:
the **single structural egress choke point** (`check_egress`, T-111) and its default-deny allow-list
(`*.atlassian.net`, GitLab/OpenSearch/Jira hosts as configured, `huggingface.co` for the one-time model
download only); the real **Confluence Cloud** ingest run against `https://tnexwm.atlassian.net`
(doctor → `mcp-ingest run --source confluence` → `status` → verify via `kb_semantic_search`) and its
generalisation to GitLab/OpenSearch/Jira (same connector path); the **least-privilege read-only Atlassian
credential** handling (env/`*_FILE` only, never committed/logged, `scrub()` both ways, `doctor` refuses a
write-capable account); the **9-source runbook** split into 4 ingestable + 5 live-only classes; and the
**real embedding-model download** path (`huggingface.co`, `HF_HUB_OFFLINE` flipped online only for that
step). The four ADR-0023 §6e adversarial/invariant tests are the highest-priority sign-off gates.
Backend-only; no frontend/UI/browser surface. Coverage target: every CHG-003 AC (FR-023…FR-027) ↔ ≥1 TC;
every Must FR (FR-023..FR-026) has ≥1 **E2E** TC; NFR-013 and NFR-014 each have ≥1 TC with a measurable
assertion.

**Out of scope for this extension.**
- **The live run against the real `tnexwm.atlassian.net` tenant with the CEO's real token** — this needs
  the CEO's real read-only token + VPN/online egress and is explicitly **NOT part of `make ci`**. It is an
  operational step gated behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true` (T-123). All CI/dev TCs run against
  **`respx` fixtures + a fake token + a model stub**. The live acceptance is captured as `@live` manual TCs
  (TC-121, TC-128, TC-131) that record the operator run but do not gate `make ci` PASS.
- **The real NFR-003 semantic-quality number and the calibrated FACT↔LOW_CONFIDENCE τ** — still
  **UNVERIFIED / TBD-with-reason** (L-002/E-004). Opening `huggingface.co` only **enables** measurement; it
  does not produce the number. The real golden-set bake-off (spike S2) is a later eval task, out of these
  ACs. TC-130 is written **CONDITIONAL/TBD**: the eval harness runs and reports a verdict distribution, the
  envelope reports `calibration_status: uncalibrated`, and **no recall/τ number is invented**.
- **Ingesting the 5 live-only sources** (CloudWatch, Kibana, Kafka, Redis, SQS/SNS) into the corpus — out;
  they are reachable + read-only + registered only (connector registry = `{confluence, gitlab, opensearch,
  jira}`; FR-026 AC-002/AC-003). TC-127 asserts an ingest for a live-only source is **refused**.
- **Any new paid Atlassian tier, any egress target beyond the configured source hosts + `huggingface.co`,
  any new network port or non-stdio transport** — out (default-deny, BR-013; read-only-to-source + stdio
  kept, BR-014/NFR-012/NFR-013). Any further vendor/egress target re-fires escalation Rule 3 → CEO.
- **Per-source read-only scopes and the exact GitLab/OpenSearch/Jira hosts** on the allow-list (Open
  question 10) — non-blocking; allow-list is configurable and default-deny holds for any unconfigured host.

## Test levels — CHG-003 (same tooling as base/CHG-001, extended)

| Level | Owner | Tool / mechanism (CHG-003 additions) |
|---|---|---|
| **Unit** | BE | `pytest` + `respx` — the `check_egress` single-choke-point structural test (one guard, no bypass path; L-001), the allow-list match/deny logic (host-not-listed → `EgressDenied`), the `scrub()` both-ways assertion on the credential/error path (forced-error, token absent), and the `doctor`/`build_server()` read-only credential gate (write-capable account → refuse). No network, no real token (fake token fixture). |
| **Integration** | BE | `pytest` against compose Postgres + `respx` replaying captured Confluence **Cloud** payloads (`tests/fixtures/confluence/*.json`) for the real `mcp-ingest run --source confluence` pipeline (crawl→redact→chunk→embed→upsert into `kb.*` under `mcp_ingest_rw`), the `status --json` read-back, and `kb_semantic_search` verify with a citation resolving to `tnexwm.atlassian.net`. The egress guard is wired on the real pull path (T-112); an unlisted host on the pull/model path → refused. Model-download path exercised with the **model stub** (CI) — the real `huggingface.co` download is `@live`. |
| **Contract** | BE produces / QA verifies | No new MCP tools are added by CHG-003 (surface count stays **61/62**). Contract check re-asserts that `ingest_run`'s `x-egress` annotation and the connector-registry enum (`confluence\|gitlab\|opensearch\|jira`) match `api-contract.yaml`, and that no write tool appears on any of the 9 servers + Jira on the real surface. |
| **E2E** | QA (stdio MCP client, via `ecc:e2e-runner`) | Spawn the actual MCP server processes over **stdio** and (a) assert **0 listening sockets / 0 outbound beyond upstream read API** for the 9 servers + Jira (regression over the relaxed egress — ADR-0023 §6e#4), (b) drive the Confluence doctor→ingest→status→verify shape end-to-end against **fixtures + fake token** (TC-111-wired guard active), (c) run the live-only `tools/list` smoke (0 write tools). The `@live` variant hits the real endpoints recorded in release-log.md and is **not** in `make ci`. |

## Environments & test data — CHG-003

- **Compose Postgres** at schema 0008 (unchanged; CHG-003 adds no schema — `implementation-plan.md`
  `schema_change: none`). Ingest writes only to `kb.*` under role `mcp_ingest_rw`.
- **Confluence Cloud fixtures** — captured **Cloud** REST payloads under `tests/fixtures/confluence/*.json`
  (`/wiki/rest/api/...`) replayed via `respx`; a **fake read-only token** fixture
  (`MCP_CONFLUENCE_API_TOKEN=fake-ro-...`) and account email. **No real credential in CI** (R-D8).
- **Egress allow-list fixtures** — a configured host (`tnexwm.atlassian.net`, `huggingface.co`) and a
  deliberately **unlisted** host (`evil.example.com` / an arbitrary external domain) to drive the
  default-deny adversarial test (TC-113).
- **Write-capable credential fixture** — a token whose sampled Confluence/Jira `operations` include a write
  verb, to drive the `doctor` refusal test (TC-116) — reusing `ConfluenceClient.credential_check`.
- **Forced-error fixture** — an upstream failure on the credential path that **interpolates the token into
  the error context**, to drive the token-never-leaks adversarial test both ways (TC-115, ties to E-003).
- **Model stub** — a local embedding stub used in CI for the download/serve path; the real `bge-m3`
  (≈2 GB, 1024d) download over `huggingface.co` is the `@live` step (TC-131).
- **Live endpoints** — the real `tnexwm.atlassian.net` + the CEO's read-only token + VPN, used **only** by
  the `@live` TCs behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, recorded in `release-log.md`, never in CI.

## Entry / exit criteria — CHG-003

**Entry (qa-plan → BE build; this document has no entry blocker):** T-111 egress guard is the **first** task
(CE1) and blocks every real CHG-003 pull (L-001 one choke point); `api-contract.yaml` CHG-003 annotations
validated; compose infra at 0008; fake-token + fixture + model-stub harness available.

**Exit (for qa-verify to report PASS on the CHG-003 slice, in CI — no live creds):**
- All P1 CHG-003 TCs pass against **fixtures + fake token + model stub** — including the **four ADR-0023
  §6e adversarial/invariant TCs**: TC-113 (egress default-deny), TC-114 (unlisted-host-refused / allow-list
  honoured), TC-115 (token-never-leaked, forced-error, scrub both ways), TC-125/TC-126 (server read-only +
  stdio unchanged, 0 ports), plus TC-116 (write-capable account refused at doctor/startup).
- **NFR-013**: 100% of unlisted-host outbound attempts refused; 9 servers + Jira open **0** network ports
  and make **0** outbound beyond their own upstream read API (TC-113, TC-114, TC-125, TC-126).
- **NFR-014**: **0** token leaks across stdout/logs/tool-results incl. the forced-error path, and 100%
  write-capable-account refusal (TC-115, TC-116); NFR-003 enablement labelled honestly (TC-130 reports
  `calibration_status: uncalibrated`, no invented number).
- `new_uncovered_ac: []` — 17/17 CHG-003 AC (FR-023…FR-027) mapped to ≥1 TC; every Must FR (FR-023..026)
  has ≥1 E2E TC.
- The `@live` TCs (TC-121, TC-128, TC-131) are **listed as live-only / not part of `make ci`** with their
  gate (`MCP_INGEST_ALLOW_LIVE_EGRESS=true` + CEO token + VPN) stated; their skip-in-CI is explicit in the
  regression report, never silently dropped (R1 discipline carried).
- Coverage ≥ 80% on changed code (egress guard, connector/ingest wiring, credential/scrub path).

## Risk-based focus — CHG-003 (riskiest areas + the extra TCs that guard them)

| Risk area (money/auth/data-loss/safety) | Why riskiest | Guard TCs |
|---|---|---|
| **Egress widening past the ingest path** (data leaving the boundary) | The 40-point hard deviation; a silent allow of an unlisted host = exfiltration channel | **TC-113** (default-deny proven, unlisted host refused — ADR-0023 §6e#1, L-001), **TC-114** (allow-list honoured; configured reached, unconfigured denied — §6e#2), **TC-112** (one `check_egress` choke point, no bypass) |
| **Credential leak** (token in logs/results) | The other 40-point hard deviation + the exact **E-003** recurring class; worse on the error path | **TC-115** (forced-error on credential path → token absent from tool result **and** stderr log, scrub both ways — §6e#3, E-003), **TC-116** (write-capable account refused at doctor/startup) |
| **Permission choke-point regression** (**E-007** class) | Real Confluence content now lands in `kb.chunks`; a restricted doc must still be excluded before assembly | **TC-124** (real-ingested restricted Confluence doc never a candidate/pack/citation; `enforce_permission` #1 unchanged — carries TC-090/TC-091, E-007) |
| **A server opening a port / making egress** | Breaks stdio/NFR-005/NFR-012 under the relaxed-egress change | **TC-125** (9 servers + Jira: 0 listening sockets, 0 outbound beyond upstream read API — §6e#4), **TC-126** (stdio round-trip unchanged, stderr-only logs) |
| **Live-only source ingested by mistake** | Would pull 5 sources never meant for the corpus | **TC-127** (`mcp-ingest run --source cloudwatch` refused; registry = 4 only) |
| **Silent-ingest data loss on an unreachable source** | A partial crawl could advance the checkpoint / mass-tombstone | **TC-119** (unreachable Confluence → `status=partial/failed`, checkpoint not advanced, no fabrication — carries FR-012/R18) |
| **Reading HF-egress as NFR-003 proof** | Repeats **E-004/L-002** | **TC-130** `# THRESHOLD TBD`: harness runs, reports verdict distribution + `uncalibrated`; **no recall/τ number invented** |
| **Live creds leaking into CI / an unintended live run** | R-D8 | `@live` TCs gated behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, fake token + model stub in CI; TC-121/128/131 marked **not in `make ci`** |

## Non-functional tests — CHG-003

| NFR | Method / tool | Threshold | Env |
|---|---|---|---|
| **NFR-013** (egress default-deny guarantee) | `check_egress` structural single-choke-point test + adversarial unlisted-host feed + process/socket inspection of the 9 servers + Jira | **100%** unlisted-host outbound refused; **0** MCP-server network ports; **0** server outbound beyond its own upstream read API | dev (TC-112, TC-113, TC-114, TC-125, TC-126) |
| **NFR-014** (read-only credential guarantee) | forced-error on the credential path + `scrub()` both-ways assertion over tool-result & stderr; `doctor`/`build_server()` read-only gate on a write-capable account | **0** token leaks (stdout/logs/results); **100%** write-capable-account refusal | dev (TC-115, TC-116) |
| **NFR-014 note** (honest NFR-003 enablement, L-002) | eval harness run reporting a verdict distribution; envelope `calibration_status` assertion | **UNVERIFIED / TBD** — recall & τ_fact/τ_low **not** asserted; `calibration_status: uncalibrated`; **no number invented** | conditional (TC-130) `# THRESHOLD TBD` |
| **NFR-003** (semantic-retrieval quality) | real golden-set bake-off (spike S2) — **later eval task, not an AC here** | **UNVERIFIED** — opening HF egress only *enables* measurement; measured value reported on the `@live` real-model run only, no bound invented | conditional / `@live` (TC-130, TC-131) `# THRESHOLD TBD` |
| NFR-012 (stdio kept — no new port, carried) | process/port inspection + stdout guard on the relaxed-egress surface | **0** listening sockets on the 9 servers + Jira; audit/log stderr-only | dev (TC-125, TC-126) |

## Security smoke — CHG-003

- **AuthZ (another caller's resource):** real-ingested Confluence content inherits team-only default-deny;
  TC-124 is the authZ smoke on the real surface — a `restricted` ingested Confluence doc must never be a
  candidate/pack/citation for an un-granted caller (choke point #1 unchanged; E-007 regression).
- **Input validation / least privilege:** the egress allow-list is default-deny (an arbitrary external host
  is refused, TC-113); the Atlassian token is least-privilege read-only and `doctor` refuses a write-capable
  account (TC-116); the connector registry rejects a live-only source as an ingest target (TC-127).
- **Secrets in responses/logs (E-003):** the token is loaded via env/`*_FILE` only, never committed, and
  `scrub()` applies on **both** the tool/result boundary and the error/log path — proven by forcing an
  error that carries the token (TC-115). No secret reaches `kb.chunks` (base R4/TC-051 carried).

## Risks — CHG-003 (QA verification angle)

| Risk | QA impact | Mitigation in this plan |
|---|---|---|
| R-D1 — an unlisted host silently allowed (egress creep) | Exfiltration channel; defeats the deviation's core guarantee | TC-113 adversarial default-deny + TC-112 single `check_egress` choke point (L-001); no bypass path |
| R-D2 — token leaks on the error path (E-003 recurrence) | Permanent, grep-able secret in logs/results | TC-115 forces an error that interpolates the token and asserts absence both ways (scrub tool-result + stderr) |
| R-D3 — write-capable Atlassian account accepted | Could mutate the source (breaks read-only-to-source) | TC-116: `doctor`/`build_server()` refuse to serve a write-capable account; only `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` bypasses (WARN each start, never for the real token) |
| R-D4 — a server opens a port / makes egress under the relaxed change (NFR-012/NFR-005) | New attack surface; stdio invariant broken | TC-125 (0 sockets, 0 outbound beyond upstream read API on 9+Jira), TC-126 (stdio round-trip, stderr-only) |
| R-D5 — restricted real Confluence doc leaks via search (E-007) | Confidentiality breach on real content | TC-124 adversarial on the real-ingested surface; choke point #1 unchanged |
| R-D6 — a live-only source ingested (CloudWatch etc.) | Pulls data never meant for the corpus | TC-127: ingest refused; registry = `{confluence,gitlab,opensearch,jira}` only |
| R-D7 — HF egress read as NFR-003 proof (E-004/L-002) | An invented recall/τ passed off as quality | TC-130 `# THRESHOLD TBD`: reports verdict distribution + `uncalibrated`, asserts no number is invented |
| R-D8 — live creds in CI / unintended live run | A real token committed or a live egress in CI | `@live` TCs (TC-121/128/131) gated behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, **not in `make ci`**; CI uses fake token + fixtures + model stub |
| R1 (carried) — real Atlassian unreachable from CI/VPN | Live Confluence acceptance may not run in CI | Fixture-based unit/integration always run; `@live` subset skipped-with-reason, must appear in the regression report, never silently dropped |
