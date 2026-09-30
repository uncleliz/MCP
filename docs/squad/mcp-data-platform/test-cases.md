# MCP Data Platform — Test Cases

> 74 test cases (TC-001…TC-074) covering all 37 AC id in `requirements.md` (FR-001…FR-015).
> Every TC's "Covers AC" cites the exact `FR-xxx/AC-xxx` id(s) it proves; several ACs have more
> than one TC (a primary positive/negative case plus a guard/regression case called out
> explicitly in `implementation-plan.md`/`architecture.md`, e.g. the Kafka auto-create guard,
> the OpenSearch `script`/PIT ban, the pgvector role-swap guard). `# THRESHOLD TBD` / conditional
> markers flag rows whose final pass bound depends on an open decision (see `test-plan.md`
> "Out of scope" and "Risks"); the test itself is not empty, only its numeric bound is pending.

## Phase 1 — Confluence (FR-001)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-001 | FR-001/AC-001 | Integration | P1 | `respx` mock for Confluence **Cloud** `GET /wiki/rest/api/content/search` returning 1 fixture page matching the query | 1. Call `confluence_search_pages{query}`. | `status=ok`; item has title, content excerpt; `citations[0].uri` is a Confluence Cloud page URL usable as citation |
| TC-002 | FR-001/AC-001 | Unit | P2 | Fixture payloads for `confluence_get_page`, `confluence_list_spaces`, `confluence_list_page_children` | 1. Map each fixture through `mappers.py`. | Each `Citation` has `locator{page_id, version}`; `uri` is an openable URL |
| TC-003 | FR-001/AC-002 | Integration | P1 | `respx` mock returns 0 hits for query `"nonexistent-xyz"` | 1. Call `confluence_search_pages{query: "nonexistent-xyz"}`. | `status=empty` (not an error, not fabricated content); `items=[]`, `citations=[]`; text rendering matches the contract's `empty` sentence exactly |
| TC-004 | FR-001/AC-003 | Unit | P1 | `mcp-confluence` `client.py` `ALLOWED_OPERATIONS` | 1. Attempt a POST/PUT/DELETE call. 2. Attempt `GET body.export_view` (excluded per ADR-0007 A1 — server-side macro rendering is an observable side effect even on GET). | Both rejected with `error.code=not_permitted`; `respx` asserts zero write requests reached Confluence |
| TC-005 | FR-001/AC-003 | E2E | P1 | `mcp-confluence` served over real stdio (subprocess) | 1. Send a JSON-RPC `tools/call` for a fabricated tool name (`confluence_create_page`) that is not in `tools/list`. | MCP SDK returns a JSON-RPC "unknown tool" protocol error; no data in Confluence is modified |
| TC-006 | FR-001/AC-003 | Integration | P2 | Startup credential check fixture: a token that **can** create content | 1. Start `mcp-confluence`. | `build_server()` refuses to serve unless `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (then logs one WARN at startup) |

## Phase 1 — GitLab (FR-002)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-007 | FR-002/AC-001 | Integration | P1 | `respx` fixtures: a project, a code blob, an MR, an issue that all exist | 1. Call `gitlab_search_projects`, `gitlab_search_code`, `gitlab_get_file`, `gitlab_list_repository_tree` with matching queries. | `status=ok`; `citations[].uri` is a GitLab **web** URL (never the API URL) |
| TC-008 | FR-002/AC-001 | Unit | P2 | Fixtures for all 11 GitLab tools' output schemas | 1. Map each fixture through `mappers.py`. | Every `GitLab*` schema (Project…ChangedFile) maps correctly with a web-link citation |
| TC-009 | FR-002/AC-002 | Integration | P1 | `respx` fixture: nonexistent project id / file path / MR iid / issue iid | 1. Call each read tool with the nonexistent identifier. | `status=not_found` for each (not an exception, not a guessed answer) |
| TC-010 | FR-002/AC-002 | Unit | P2 | Tools whose name contains the substring "merge" (`gitlab_list_merge_requests`, `gitlab_get_merge_request`) | 1. Run the readonly tool-surface deny-check. | Both tools remain exposed and are **not** flagged as write operations — deny-regex-by-name was demoted to a warning per ADR-0003 A2 precisely because of this false positive |
| TC-011 | FR-002/AC-003 | Unit | P1 | GitLab `client.py` `ALLOWED_OPERATIONS` | 1. Attempt create-issue / merge-MR / push (POST/PUT to `/api/v4/...`). | `not_permitted`; `respx` confirms zero write calls reached GitLab |
| TC-012 | FR-002/AC-003 | Integration | P1 | Two PAT fixtures: scope `api` (write) vs. scope `read_api`/`read_repository` | 1. Start `mcp-gitlab` with each PAT in turn (`GET /api/v4/personal_access_tokens/self`). | Write-scoped PAT → serve refused; read-scoped PAT → serve succeeds |
| TC-013 | FR-002/AC-003 | Unit | P2 | `MCP_GITLAB_PATH_DENY` deny-glob covering `.env`/`*.pem` | 1. Call `gitlab_get_file` for a denied path. | `not_permitted`; file content is never returned |

## Phase 1 — Journey 1: cross-source dev knowledge lookup (FR-003)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-014 | FR-003/AC-001 | E2E | P1 | `mcp-confluence` + `mcp-gitlab` served over stdio; fixtures containing relevant content in **both** sources; `dev_knowledge_lookup` prompt registered | 1. Drive the prompt with a scripted MCP client that performs the real tool-calling round trip a Claude client would perform. | Final synthesized answer includes ≥1 Confluence citation **and** ≥1 GitLab citation |
| TC-015 | FR-003/AC-002 | E2E | P1 | Same setup, but the Confluence fixture returns `status=empty` for the question while GitLab returns matches | 1. Drive the same prompt. | Answer explicitly states no matching Confluence documentation was found for that source (not a fabricated citation); the GitLab citation is still present |

## Phase 2 — OpenSearch (FR-004)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-016 | FR-004/AC-001 | Integration | P1 | Mocked `opensearch-py` client with matching log entries in the given index + time range | 1. Call `opensearch_search_logs{index_pattern, query, time_from, time_to}`. | `status=ok`; each item cites index name + document `_id` + `@timestamp` |
| TC-017 | FR-004/AC-002 | Integration | P1 | Case A: no matches. Case B: `time_from > time_to` | 1. Call `opensearch_search_logs` for each case. | A → `status=empty`; B → `error.code=invalid_input`; neither fabricates log entries |
| TC-018 | FR-004/AC-002 | Unit | P1 | Request bodies containing `script`, `scripted_metric`, `runtime_mappings`, `scroll`, `point_in_time` | 1. Call `opensearch_search_dsl` with each forbidden construct. | `not_permitted` for every construct (ADR-0008 A2 — these create cluster-side state) |

## Phase 2 — Kibana (FR-005)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-019 | FR-005/AC-001 | Integration | P1 | `respx` fixture: a saved dashboard matching the requested service/topic | 1. Call `kibana_find_saved_objects`. 2. Call `kibana_build_dashboard_link{id, time_from, time_to}`. | `status=ok`; the built link opens the correct dashboard id with the exact `_g=(time:(from,to))` requested; `build_dashboard_link` makes **no** network call |
| TC-020 | FR-005/AC-002 | Integration | P1 | No dashboard/visualization matches the requested topic | 1. Call `kibana_find_saved_objects`. | `status=not_found` |

## Phase 2 — CloudWatch (FR-006)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-021 | FR-006/AC-001 | Integration | P1 | `botocore` Stubber with a matching log group/metric/alarm + time window | 1. Call `cloudwatch_filter_log_events`, `cloudwatch_get_metric_data`, `cloudwatch_describe_alarms`. | `status=ok`; citation includes the log group/metric/alarm name + time range |
| TC-022 | FR-006/AC-002 | Integration | P1 | Invalid/nonexistent log group, metric, alarm name | 1. Call the same 3 tools with bad identifiers. | Explicit error/empty status for each; never fabricated data |
| TC-023 | FR-006/AC-001 | Integration | P2 | `cloudwatch_run_logs_insights` in progress (`StartQuery`) | 1. Cancel the tool call mid-flight before the query finishes. | `StopQuery` is called exactly once, inside `try/finally` + `asyncio.shield` — no orphaned running Insights query |

## Phase 2 — Kafka (FR-007)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-024 | FR-007/AC-001 | Integration | P1 | Kafka KRaft (docker-compose) with an existing topic + sample messages | 1. Call `kafka_describe_topic`. 2. Call `kafka_peek_messages`. | `status=ok`; citation = topic + partition + offset (offset is a **string**) |
| TC-025 | FR-007/AC-002 | Integration | P1 | Nonexistent topic name; broker configured with `auto.create.topics.enable=true` | 1. Snapshot the topic list. 2. Call `kafka_describe_topic` with the nonexistent name. 3. Snapshot the topic list again. | `status=not_found`; **topic list before/after is identical** — auto-create did not silently fire (R17/ADR-0009 A1 negative test; this is the case where the AC-002 negative test could itself become a write) |
| TC-026 | FR-007/AC-003 | Unit | P1 | Kafka `client.py`/`ports.py` (`KafkaReader`) source | 1. Inspect all code paths for `Producer` instantiation, `subscribe()`, or commit calls. | Zero `Producer` instances anywhere; consumer uses `assign()`+`seek()`, `enable.auto.commit=false`, `allow.auto.create.topics=false`, and never passes `topic=` into a metadata request |
| TC-027 | FR-007/AC-003 | Integration | P2 | `kafka_describe_consumer_group` called against a group actively used by another live consumer | 1. Record that consumer's committed offsets. 2. Call `kafka_describe_consumer_group{group}`. 3. Re-check the committed offsets. | Unchanged — peek/describe never commits |
| TC-028 | FR-007/AC-003 | Unit | P2 | `# CLIENT TBD (spike S4 / ADR-0009 — confluent-kafka vs kafka-python, not yet closed)`. Test is written against the `KafkaReader` port interface only. | 1. Run the Kafka tool test suite against whichever adapter implementation S4 currently selects. | All tests above (TC-024…027) pass unmodified regardless of the concrete client library — proves the R7 mitigation; re-run as-is once ADR-0009 closes, no test changes needed |

## Phase 2 — Redis (FR-008)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-029 | FR-008/AC-001 | Integration | P1 | `redis:7` (docker-compose) with `mcp_ro` ACL user; a key pre-seeded | 1. Call `redis_get_key{key}`. | `status=ok`; value + data type returned with the key name as citation |
| TC-030 | FR-008/AC-002 | Integration | P1 | Key does not exist | 1. Call `redis_get_key{key: "missing"}`. | Explicit nil/`not_found` response, not a fabricated value |
| TC-031 | FR-008/AC-003 | Unit | P1 | Redis `client.py` allowlist | 1. Attempt `SET`/`DEL`/`EXPIRE`. 2. Attempt `KEYS` (banned in favor of `SCAN`). | `not_permitted` for every write command; `KEYS` does not appear in any code path |
| TC-032 | FR-008/AC-003 | Integration | P2 | ACL startup check with a user that **has** a write category granted | 1. Start `mcp-redis`. | Serve refused (`ACL GETUSER` shows a write category) unless `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` |

## Phase 2 — Journey 2: cross-source incident investigation (FR-009)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-033 | FR-009/AC-001 | E2E | P1 | `mcp-cloudwatch` + `mcp-opensearch` + `mcp-kibana` served over stdio; fixtures with relevant data for a service+time window; `incident_investigation` prompt registered | 1. Drive the prompt with a scripted MCP client. | Consolidated answer includes a log excerpt (OpenSearch), metric/alarm data (CloudWatch), and a dashboard link (Kibana), each individually cited to its own source |
| TC-034 | FR-009/AC-002 | E2E | P1 | Same setup, but CloudWatch alarms fixture returns `status=empty` for the window | 1. Drive the same prompt. | Answer explicitly states the gap (e.g. "no CloudWatch alarms found in this window") instead of inferring or fabricating |

## Phase 3 — SQS/SNS (FR-010)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-035 | FR-010/AC-001 | Integration | P1 | LocalStack (`sqs,sns`) with 1 queue + 1 topic seeded | 1. Call `sqs_get_queue_attributes`. 2. Call `sns_get_topic_attributes`. | `status=ok`; metadata includes `ApproximateNumberOfMessages` and name/ARN as citation |
| TC-036 | FR-010/AC-002 | Integration | P1 | Nonexistent queue/topic name | 1. Call both attribute tools with a bad name. | `status=not_found` |
| TC-037 | FR-010/AC-003 | Unit | P1 | `mcp-sqs-sns` `client.py` allowlist | 1. Attempt `SendMessage`/`DeleteMessage`/`Publish`. 2. Attempt `ReceiveMessage` (excluded — it mutates visibility timeout, a side effect). | `not_permitted` for all four; no code path calls `ReceiveMessage` |
| TC-038 | FR-010/AC-003 | Integration | P2 | Full `mcp-sqs-sns` suite run against LocalStack | 1. Count messages on all LocalStack queues before the run. 2. Run the full suite. 3. Count again. | Message counts identical before/after |

## Phase 3 — Postgres+pgvector query server (FR-011)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-039 | FR-011/AC-001 | Integration | P1 | pgvector (docker-compose) seeded with chunks relevant to a sample query | 1. Call `kb_semantic_search{query, top_k}`. | `status=ok`; top-N chunks returned, each with original `source_type` + original-source URI (not just `document_id`) as citation |
| TC-040 | FR-011/AC-001 | Integration | P1 | `eval/recall_queries.yaml` (50 sample queries); compose Postgres populated via the ingest pipeline | 1. Run all 50 queries through `kb_semantic_search` with HNSW. 2. Re-run with `SET enable_indexscan=off` (brute-force ground truth). 3. Compare top-k sets. | recall ≥ 0.95 against brute-force baseline (ADR-0011 A3 gate, also the one concrete NFR-003-adjacent number this plan can assert today) |
| TC-041 | FR-011/AC-002 | Integration | P1 | No embedded chunk meets `min_similarity` (best similarity, computed **without** filters, is below threshold) | 1. Call `kb_semantic_search` with a query far from any embedded content. | `status=empty`; `meta.warnings` states `best_similarity < min_similarity` — not presented as a confident low-relevance match |
| TC-042 | FR-011/AC-002 | Integration | P1 | A chunk exists with high similarity, but `source_types`/`container`/`updated_after` filters exclude it | 1. Call `kb_semantic_search` with filters that exclude the otherwise-best match. | `status=empty`, **but** `meta.warnings` distinguishes "filters excluded N results" from "nothing is similar at all" — regression test for the HNSW post-filter false-negative that ADR-0011 A3 exists to prevent |
| TC-043 | FR-011/AC-003 | Unit | P1 | `MCP_PGVECTOR_DSN` mistakenly set to the `mcp_ingest_rw` (write-capable) role's DSN | 1. Start `mcp-pgvector`. | Serve refused — startup check `has_table_privilege('kb.chunks','INSERT') = false` fails; this is the exact vulnerability ADR-0003 A1 patches |
| TC-044 | FR-011/AC-003 | Integration | P2 | `mcp_query_ro` role, `BEGIN READ ONLY` transaction | 1. Attempt an INSERT/UPDATE/DELETE via the tool's parameterized SQL layer. | Rejected at the DB role level; no tool exposes such an operation to begin with (there is no `postgres_query(sql)` tool) |

## Phase 3 — Ingest/embedding pipeline (FR-012)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-045 | FR-012/AC-001 | Integration | P1 | Compose Postgres; Confluence/GitLab connector fixtures with new/changed content | 1. Run `mcp-ingest run --source confluence,gitlab --mode incremental`. 2. Query the same content via `kb_semantic_search`. | Content is embedded and stored with source metadata, and is retrievable with a citation resolving to the original item (round-trip; also re-proves FR-011/AC-001) |
| TC-046 | FR-012/AC-002 | Integration | P1 | One configured source (e.g. GitLab) simulated unreachable mid-run while Confluence succeeds | 1. Run `mcp-ingest run --source all`. | Run `status=partial`; failure recorded for GitLab; Confluence still commits; Confluence's previously stored embeddings are not corrupted; GitLab's checkpoint does **not** advance past the failed document's watermark (ADR-0012 A2) |
| TC-047 | FR-012/AC-002 | Integration | P1 | `--mode full` reconcile where the crawl dies at 30% of `documents_seen` | 1. Run `mcp-ingest run --mode full` with the simulated failure. | Safety valve blocks tombstoning (`documents_seen < 0.8×` existing) → `IngestError{stage: reconcile}`; **no** document is tombstoned |
| TC-048 | FR-012/AC-002 | Integration | P2 | A document fails `MCP_INGEST_MAX_DOC_RETRIES` times | 1. Inspect `kb.ingest_failures`. 2. Run `mcp-ingest run --retry-failed`. | Failing doc appears in `ingest_failures` with `attempts`/`stage`/`code`; `--retry-failed` re-ingests it and removes the row on success |
| TC-049 | FR-012/AC-003 | Integration | P1 | Previously ingested content changed at the source (new `content_hash`) | 1. Run `mcp-ingest run` once. 2. Change the source content. 3. Run it again. | Stored embedding/metadata is updated/replaced, not duplicated (`UNIQUE(source_type, source_id)`); two runs on **unchanged** content also produce no duplicates |
| TC-050 | FR-012/AC-003 | Integration | P2 | `content_hash` unchanged but `title`/`source_uri` changed (e.g. a Confluence page renamed or moved to another space) | 1. Re-run ingest for that document. | Chunk+embed is skipped (hash match), but `title`/`source_uri`/`container`/`author`/`last_seen_*` are updated regardless — otherwise citation goes stale (ADR-0012 A4) |
| TC-051 | FR-012/AC-001 | Integration | P1 | Source content containing a secret-looking token (e.g. an `.env`-style line, an API-key pattern) | 1. Run ingest over that content. 2. Query `kb_semantic_search` for it. | The secret never appears in `kb.chunks` nor in the search result; content blocked by deny-glob shows up in `kb.ingest_failures{stage: redact, code: blocked_by_policy}` instead (R4 regression) |

## Phase 3 — Journey 3: semantic + messaging synthesis (FR-013)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-052 | FR-013/AC-001 | E2E | P1 | `mcp-pgvector` + `mcp-sqs-sns` served over stdio; seeded embedded chunks + a seeded queue; `semantic_synthesis` prompt registered | 1. Drive the prompt with a scripted MCP client. | Answer cites the **original** source(s) behind each matched embedding (original URL, not `document_id`) and the queue/topic reference when SQS/SNS data was used |
| TC-053 | FR-013/AC-002 | E2E | P1 | pgvector query configured to return `status=empty` | 1. Drive the same prompt with an off-topic question. | Answer explicitly states no relevant indexed data was found; does not fabricate an answer |

## Cross-cutting — read-only enforcement, all 9 servers (FR-014)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-054 | FR-014/AC-001 | Unit | P1 | `tools.snapshot.json` for each of the 9 packages | 1. Run `assert_readonly_tool_surface` for each snapshot against `api-contract.yaml`. | Zero tools with a write/update/delete side effect across the full 49-tool surface; every operation carries `x-readonly: true`/`x-side-effects: none` |
| TC-055 | FR-014/AC-001 | Unit | P1 | Transport-assertion fixture active for every unit test (`respx`) | 1. Run the full unit suite for all 9 packages. | Every outbound request is `GET`/`HEAD` except explicit allowlisted `(host, method, path)` tuples; any `POST`/`PUT`/`DELETE` fails the test immediately |
| TC-056 | FR-014/AC-002 | E2E | P1 | Each of the 9 servers served over real stdio | 1. Send a JSON-RPC `tools/call` for a fabricated/nonexistent "write" tool name (e.g. `confluence_delete_page`) to each server in turn. | MCP SDK returns the JSON-RPC "unknown tool" **protocol** error (not an `ErrorEnvelope` — per `api-contract.yaml` `info.description`, this AC lives at the protocol layer, not the contract-schema layer); no source data changes for any of the 9 |
| TC-057 | FR-014/AC-002 | Unit | P2 | A request targeting an operation that **does** exist but is outside the allowlist (e.g. an OpenSearch body containing `script`) | 1. Call the tool with the forbidden construct. | `error.code=not_permitted` — distinct mechanism from TC-056's protocol-layer case; the contract explicitly calls out that these are two different things and a tester should not conflate them |

## Cross-cutting — traceability / no-hallucination (FR-015)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-058 | FR-015/AC-001 | Unit | P1 | `mcp_common.envelope` invariant | 1. Attempt to construct `ToolResult(status=ok, citations=[])`. | Construction fails validation; golden-file render snapshot test covers all 4 status branches × a representative list-tool and detail-tool |
| TC-059 | FR-015/AC-001 | Integration | P1 | At least one real tool call per server returning `status=ok` | 1. Inspect the text rendering for each. | Response includes a resolvable citation (link/id) for every distinct source used, `[n]` markers matching `citation_ref` |
| TC-060 | FR-015/AC-002 | Unit | P1 | All relevant tool calls in a scenario return `empty`/`not_found` | 1. Render the text output. | Response explicitly states no matching data was found across the queried sources; the `Nguồn:`/"Sources" section is entirely absent, never fabricated |

## Non-functional requirements

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-061 | FR-014/AC-001, FR-014/AC-002 (NFR-001) | Integration | P1 | Full `test_tools_readonly.py` suite across all 9 packages + `mcp-ingest` connector methods | 1. Run the suite for every package before each phase sign-off. | 100% of attempted mutating operations across all 9 servers are rejected; 0 exceptions |
| TC-062 | FR-001/AC-002, FR-002/AC-002 (NFR-002) | Unit | P1 | Simulated unreachable endpoint (`respx` `ConnectError`) for each HTTP-based server | 1. Call any tool against the dead endpoint. | `error.code ∈ {upstream_timeout, upstream_unavailable}`; exactly 2 attempts (1 retry); total elapsed ≈ 21s and strictly **< 25s** deadline. `# THRESHOLD TBD (Open question 4 — PO has not confirmed the final bound; interim assertion uses the numbers architecture.md/ADR-0006 A2 already committed: connect 3s / read 7s / 2 attempts / 1s backoff ⇒ 2×(3+7)+1 = 21s < 25s)` |
| TC-063 | NFR-002 | Integration | P1 | `ThreadPoolExecutor(max_workers=4)` saturated with 4 hung synchronous SDK calls (CloudWatch/Kafka) | 1. Issue a 5th tool call while all 4 workers are blocked. | The 5th call returns `upstream_unavailable` immediately — does not wait for a free worker (R16 regression) |
| TC-064 | NFR-002 | Unit | P2 | Kafka `socket.timeout.ms`/`metadata.request.timeout.ms=8000`, Redis connect 2s/read 5s, Postgres `connect_timeout=3`/`statement_timeout=15s` | 1. Simulate an unreachable broker/Redis/Postgres for each. | Each surfaces a distinguishable timeout error within its configured bound; none hangs indefinitely |
| TC-065 | NFR-003 | Manual | P2 | `eval/questions.yaml`, ≥10 questions per phase with `expected_sources`/`expected_behavior` | 1. Run `scripts/run_eval.py`. 2. Manually review each answer against expectations. | Proportion of answers with a valid, resolvable citation when tool data was used is recorded and reported. `# THRESHOLD TBD (Open question 1 — exact pass threshold to be set by PO after Phase 1 launch; this run only produces the measured proportion for that decision, it does not itself gate PASS/FAIL)` |
| TC-066 | FR-011/AC-001 (NFR-003) | Integration | P1 | Same setup as TC-040 | 1. See TC-040. | recall ≥ 0.95 — the one concrete NFR-003-adjacent number this plan can assert today, independent of the PO threshold pending in TC-065 |
| TC-067 | NFR-004 | Integration | P2 | `mcp-ingest run` completed at least once | 1. Run `mcp-ingest status --json`. | Output matches the `IngestStatusRow` schema; `staleness_hours` computed correctly per source. `# THRESHOLD TBD (Open question 5 — acceptable freshness bound not yet set by PO/SA; architecture.md records SA's *candidate* proposal — incremental hourly + full reconcile at 03:00 + staleness ≤4h during business hours — as not yet confirmed. This test only asserts the number is reported correctly, not that it passes a bound)` |
| TC-068 | NFR-004 | Integration | P2 | Populated DB vs. an empty DB | 1. Call `kb_list_sources` in both states. | Populated → `meta.data_freshness` with `last_ingested_at` + `staleness_hours` + doc/chunk counts; empty DB → `status=empty`, not an error |
| TC-069 | NFR-005 | Unit | P1 | Stdout guard active in every unit test, every package; a `print()` deliberately injected into a tool body for this test only | 1. Call any tool while the injected `print()` fires. | Test fails immediately on any byte written to stdout outside JSON-RPC framing; all structured logs go to stderr only |
| TC-070 | NFR-005 | Manual | P2 | `mcp-common config-emit --server <name>` output pasted into `claude_desktop_config.json` | 1. Register each of the 9 servers in Claude Desktop/Code. 2. Issue one tool call per server. | Each server appears in the MCP tool list and responds, with no separate UI/web service required |

## Open-decision markers (Gate B / spikes — kept distinct for `qa-verify` re-run)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-071 | FR-012/AC-001, FR-012/AC-003 | Integration | P2 | `# MODEL TBD (spike S2 / ADR-0010 — bge-m3 vs multilingual-e5-large; both candidates are 1024d so no schema change is needed either way)` | 1. Run `scripts/bakeoff_embedding.py` comparing both candidate models on the NFR-003 question set. | Recall@k / latency / RAM / load-time comparison table produced; whichever model S2 ultimately selects, TC-039/TC-040/TC-066 must be re-run against the final choice — no test rewrite needed, only re-execution |
| TC-072 | FR-012/AC-001 (ADR-0016 Part 2, Open question 3) | Integration | P2 | **CONDITIONAL — only runs if Gate B/PO selects "corpus may contain `restricted` visibility"** (the "T-067 equivalent" branch) | 1. Query `kb_semantic_search` as two identities with different permission groups over a corpus containing both `team` and `restricted` documents. | Restricted documents are filtered out for the identity without access, present for the identity with access. `# RBAC SCOPE TBD (Open question 3 — not yet answered; this TC is a placeholder and does not execute until Gate B decides)` |
| TC-073 | FR-012/AC-001 | Integration | P1 | The **currently-assumed default** branch if Gate B answers "no restricted content" — active unless/until Open question 3 says otherwise | 1. Run `mcp-ingest run` against a fixture containing a document whose inferred `visibility != 'team'`. | Document is rejected at the redact/tagging stage and recorded in `kb.ingest_failures{code: blocked_by_policy}`; it never reaches `kb.chunks` |
| TC-074 | FR-012/AC-003 | Integration | P2 | `# RETENTION DEFAULT TBD (mcp-ingest prune has no implicit default; api-contract.yaml requires at least one of tombstoned/older_than_days to be passed explicitly)` | 1. Run `mcp-ingest prune` with no flags. 2. Run `mcp-ingest prune --tombstoned --older-than 30d --dry-run`. | Step 1 is rejected by the request schema (`anyOf: [tombstoned, older_than_days]` required); step 2 reports `documents_deleted`/`chunks_deleted` without modifying data (`dry_run` default `true`) |

## AC → TC traceability summary

All 37 AC id from `requirements.md` map to at least one TC above:

| FR | AC ids | TC ids |
|---|---|---|
| FR-001 | AC-001, AC-002, AC-003 | TC-001, TC-002, TC-003, TC-004, TC-005, TC-006 |
| FR-002 | AC-001, AC-002, AC-003 | TC-007, TC-008, TC-009, TC-010, TC-011, TC-012, TC-013 |
| FR-003 | AC-001, AC-002 | TC-014, TC-015 |
| FR-004 | AC-001, AC-002 | TC-016, TC-017, TC-018 |
| FR-005 | AC-001, AC-002 | TC-019, TC-020 |
| FR-006 | AC-001, AC-002 | TC-021, TC-022, TC-023 |
| FR-007 | AC-001, AC-002, AC-003 | TC-024, TC-025, TC-026, TC-027, TC-028 |
| FR-008 | AC-001, AC-002, AC-003 | TC-029, TC-030, TC-031, TC-032 |
| FR-009 | AC-001, AC-002 | TC-033, TC-034 |
| FR-010 | AC-001, AC-002, AC-003 | TC-035, TC-036, TC-037, TC-038 |
| FR-011 | AC-001, AC-002, AC-003 | TC-039, TC-040, TC-041, TC-042, TC-043, TC-044, TC-066, TC-071 |
| FR-012 | AC-001, AC-002, AC-003 | TC-045, TC-046, TC-047, TC-048, TC-049, TC-050, TC-051, TC-071, TC-072, TC-073, TC-074 |
| FR-013 | AC-001, AC-002 | TC-052, TC-053 |
| FR-014 | AC-001, AC-002 | TC-054, TC-055, TC-056, TC-057, TC-061 |
| FR-015 | AC-001, AC-002 | TC-058, TC-059, TC-060 |
| NFR-001 | — | TC-061 |
| NFR-002 | — | TC-062, TC-063, TC-064 |
| NFR-003 | — | TC-065, TC-066 |
| NFR-004 | — | TC-067, TC-068 |
| NFR-005 | — | TC-069, TC-070 |

`uncovered_ac: []` — 37/37 AC id covered.
