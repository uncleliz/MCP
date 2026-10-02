# MCP Data Platform — Test Cases

> 77 test cases (TC-001…TC-077; TC-072 closed as not applicable) covering all 37 AC id in `requirements.md` (FR-001…FR-015).
> Every TC's "Covers AC" cites the exact `FR-xxx/AC-xxx` id(s) it proves; several ACs have more
> than one TC (a primary positive/negative case plus a guard/regression case called out
> explicitly in `implementation-plan.md`/`architecture.md`, e.g. the Kafka auto-create guard,
> the OpenSearch `script`/PIT ban, the pgvector role-swap guard). `# THRESHOLD TBD` / conditional
> markers flag rows whose final pass bound depends on an open decision (see `test-plan.md`
> "Out of scope" and "Risks"); the test itself is not empty, only its numeric bound is pending.

## Phase 1 — Confluence (FR-001)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-001 | FR-001/AC-001 | integration | P1 | `respx` mock for Confluence **Cloud** `GET /wiki/rest/api/content/search` returning 1 fixture page matching the query | 1. Call `confluence_search_pages{query}`. | `status=ok`; item has title, content excerpt; `citations[0].uri` is a Confluence Cloud page URL usable as citation |
| TC-002 | FR-001/AC-001 | unit | P2 | Fixture payloads for `confluence_get_page`, `confluence_list_spaces`, `confluence_list_page_children` | 1. Map each fixture through `mappers.py`. | Each `Citation` has `locator{page_id, version}`; `uri` is an openable URL |
| TC-003 | FR-001/AC-002 | integration | P1 | `respx` mock returns 0 hits for query `"nonexistent-xyz"` | 1. Call `confluence_search_pages{query: "nonexistent-xyz"}`. | `status=empty` (not an error, not fabricated content); `items=[]`, `citations=[]`; text rendering matches the contract's `empty` sentence exactly |
| TC-004 | FR-001/AC-003 | unit | P1 | `mcp-confluence` `client.py` `ALLOWED_OPERATIONS` | 1. Attempt a POST/PUT/DELETE call. 2. Attempt `GET body.export_view` (excluded per ADR-0007 A1 — server-side macro rendering is an observable side effect even on GET). | Both rejected with `error.code=not_permitted`; `respx` asserts zero write requests reached Confluence |
| TC-005 | FR-001/AC-003 | E2E | P1 | `mcp-confluence` served over real stdio (subprocess) | 1. Send a JSON-RPC `tools/call` for a fabricated tool name (`confluence_create_page`) that is not in `tools/list`. | MCP SDK returns a JSON-RPC "unknown tool" protocol error; no data in Confluence is modified |
| TC-006 | FR-001/AC-003 | integration | P2 | Startup credential check fixture: a token that **can** create content | 1. Start `mcp-confluence`. | `build_server()` refuses to serve unless `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (then logs one WARN at startup) |

## Phase 1 — GitLab (FR-002)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-007 | FR-002/AC-001 | integration | P1 | `respx` fixtures: a project, a code blob, an MR, an issue that all exist | 1. Call `gitlab_search_projects`, `gitlab_search_code`, `gitlab_get_file`, `gitlab_list_repository_tree` with matching queries. | `status=ok`; `citations[].uri` is a GitLab **web** URL (never the API URL) |
| TC-008 | FR-002/AC-001 | unit | P2 | Fixtures for all 11 GitLab tools' output schemas | 1. Map each fixture through `mappers.py`. | Every `GitLab*` schema (Project…ChangedFile) maps correctly with a web-link citation |
| TC-009 | FR-002/AC-002 | integration | P1 | `respx` fixture: nonexistent project id / file path / MR iid / issue iid | 1. Call each read tool with the nonexistent identifier. | `status=not_found` for each (not an exception, not a guessed answer) |
| TC-010 | FR-002/AC-002 | unit | P2 | Tools whose name contains the substring "merge" (`gitlab_list_merge_requests`, `gitlab_get_merge_request`) | 1. Run the readonly tool-surface deny-check. | Both tools remain exposed and are **not** flagged as write operations — deny-regex-by-name was demoted to a warning per ADR-0003 A2 precisely because of this false positive |
| TC-011 | FR-002/AC-003 | unit | P1 | GitLab `client.py` `ALLOWED_OPERATIONS` | 1. Attempt create-issue / merge-MR / push (POST/PUT to `/api/v4/...`). | `not_permitted`; `respx` confirms zero write calls reached GitLab |
| TC-012 | FR-002/AC-003 | integration | P1 | Two PAT fixtures: scope `api` (write) vs. scope `read_api`/`read_repository` | 1. Start `mcp-gitlab` with each PAT in turn (`GET /api/v4/personal_access_tokens/self`). | Write-scoped PAT → serve refused; read-scoped PAT → serve succeeds |
| TC-013 | FR-002/AC-003 | unit | P2 | `MCP_GITLAB_PATH_DENY` deny-glob covering `.env`/`*.pem` | 1. Call `gitlab_get_file` for a denied path. | `not_permitted`; file content is never returned |

## Phase 1 — Journey 1: cross-source dev knowledge lookup (FR-003)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-014 | FR-003/AC-001 | E2E | P1 | `mcp-confluence` + `mcp-gitlab` served over stdio; fixtures containing relevant content in **both** sources; `dev_knowledge_lookup` prompt registered | 1. Drive the prompt with a scripted MCP client that performs the real tool-calling round trip a Claude client would perform. | Final synthesized answer includes ≥1 Confluence citation **and** ≥1 GitLab citation |
| TC-015 | FR-003/AC-002 | E2E | P1 | Same setup, but the Confluence fixture returns `status=empty` for the question while GitLab returns matches | 1. Drive the same prompt. | Answer explicitly states no matching Confluence documentation was found for that source (not a fabricated citation); the GitLab citation is still present |

## Phase 2 — OpenSearch (FR-004)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-016 | FR-004/AC-001 | integration | P1 | Mocked `opensearch-py` client with matching log entries in the given index + time range | 1. Call `opensearch_search_logs{index_pattern, query, time_from, time_to}`. | `status=ok`; each item cites index name + document `_id` + `@timestamp` |
| TC-017 | FR-004/AC-002 | integration | P1 | Case A: no matches. Case B: `time_from > time_to` | 1. Call `opensearch_search_logs` for each case. | A → `status=empty`; B → `error.code=invalid_input`; neither fabricates log entries |
| TC-018 | FR-004/AC-002 | unit | P1 | `MCP_OPENSEARCH_ALLOW_DSL=true` (tool is not registered otherwise — ADR-0008 A7). Request bodies containing `script`, `scripted_metric`, `runtime_mappings`, `scroll`, `point_in_time` | 1. Call `opensearch_search_dsl` with each forbidden construct. | `not_permitted` for every construct (ADR-0008 A2 — these create cluster-side state) |

## Phase 2 — Kibana (FR-005)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-019 | FR-005/AC-001 | integration | P1 | `respx` fixture: a saved dashboard matching the requested service/topic | 1. Call `kibana_find_saved_objects`. 2. Call `kibana_build_dashboard_link{id, time_from, time_to}`. | `status=ok`; the built link opens the correct dashboard id with the exact `_g=(time:(from,to))` requested; `build_dashboard_link` makes **exactly one** verifying `GET /api/saved_objects/dashboard/{id}` (anti-fabricated-id guard, FR-015; also yields `title`) — `respx` asserts call count == 1; the URL itself is built by a pure function |
| TC-020 | FR-005/AC-002 | integration | P1 | No dashboard/visualization matches the requested topic; an unknown saved-object id (`respx` 404) | 1. Call `kibana_find_saved_objects` with the unmatched topic. 2. Call `kibana_get_saved_object{id: unknown}`. 3. Call `kibana_build_dashboard_link{id: unknown, ...}`. | Step 1 → `status=empty` (search with no match = `empty` per `ResultStatus` convention; this is the explicit "not found" outcome FR-005/AC-002 requires — not fabricated). Steps 2 and 3 → `status=not_found` (specific identifier); no link is returned for an unknown id |

## Phase 2 — CloudWatch (FR-006)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-021 | FR-006/AC-001 | integration | P1 | `botocore` Stubber with a matching log group/metric/alarm + time window | 1. Call `cloudwatch_filter_log_events`, `cloudwatch_get_metric_data`, `cloudwatch_describe_alarms`. | `status=ok`; citation includes the log group/metric/alarm name + time range |
| TC-022 | FR-006/AC-002 | integration | P1 | Invalid/nonexistent log group, metric, alarm name | 1. Call the same 3 tools with bad identifiers. | Explicit error/empty status for each; never fabricated data |
| TC-023 | FR-006/AC-001 | integration | P2 | `cloudwatch_run_logs_insights` in progress (`StartQuery`) | 1. Cancel the tool call mid-flight before the query finishes. | `StopQuery` is called exactly once, inside `try/finally` + `asyncio.shield` — no orphaned running Insights query |

## Phase 2 — Kafka (FR-007)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-024 | FR-007/AC-001 | integration | P1 | Kafka KRaft (docker-compose) with an existing topic + sample messages | 1. Call `kafka_describe_topic`. 2. Call `kafka_peek_messages`. | `status=ok`; citation = topic + partition + offset (offset is a **string**) |
| TC-025 | FR-007/AC-002 | integration | P1 | Nonexistent topic name; broker configured with `auto.create.topics.enable=true` | 1. Snapshot the topic list. 2. Call `kafka_describe_topic` with the nonexistent name. 3. Snapshot the topic list again. | `status=not_found`; **topic list before/after is identical** — auto-create did not silently fire (R17/ADR-0009 A1 negative test; this is the case where the AC-002 negative test could itself become a write) |
| TC-026 | FR-007/AC-003 | unit | P1 | Kafka `client.py`/`ports.py` (`KafkaReader`) source | 1. Inspect all code paths for `Producer` instantiation, `subscribe()`, or commit calls. | Zero `Producer` instances anywhere; consumer uses `assign()`+`seek()`, `enable.auto.commit=false`, `allow.auto.create.topics=false`, and never passes `topic=` into a metadata request |
| TC-027 | FR-007/AC-003 | integration | P2 | `kafka_describe_consumer_group` called against a group actively used by another live consumer | 1. Record that consumer's committed offsets. 2. Call `kafka_describe_consumer_group{group}`. 3. Re-check the committed offsets. | Unchanged — peek/describe never commits |
| TC-028 | FR-007/AC-003 | unit | P2 | `# CLIENT TBD (spike S4 / ADR-0009 — confluent-kafka vs kafka-python, not yet closed)`. Test is written against the `KafkaReader` port interface only. | 1. Run the Kafka tool test suite against whichever adapter implementation S4 currently selects. | All tests above (TC-024…027) pass unmodified regardless of the concrete client library — proves the R7 mitigation; re-run as-is once ADR-0009 closes, no test changes needed |

## Phase 2 — Redis (FR-008)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-029 | FR-008/AC-001 | integration | P1 | `redis:7` (docker-compose) with `mcp_ro` ACL user; a key pre-seeded | 1. Call `redis_get_key{key}`. | `status=ok`; value + data type returned with the key name as citation |
| TC-030 | FR-008/AC-002 | integration | P1 | Key does not exist | 1. Call `redis_get_key{key: "missing"}`. | Explicit nil/`not_found` response, not a fabricated value |
| TC-031 | FR-008/AC-003 | unit | P1 | Redis `client.py` allowlist | 1. Attempt `SET`/`DEL`/`EXPIRE`. 2. Attempt `KEYS` (banned in favor of `SCAN`). | `not_permitted` for every write command; `KEYS` does not appear in any code path |
| TC-032 | FR-008/AC-003 | integration | P2 | ACL startup check with a user that **has** a write category granted | 1. Start `mcp-redis`. | Serve refused (`ACL GETUSER` shows a write category) unless `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` |

## Phase 2 — Journey 2: cross-source incident investigation (FR-009)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-033 | FR-009/AC-001 | E2E | P1 | `mcp-cloudwatch` + `mcp-opensearch` + `mcp-kibana` served over stdio; fixtures with relevant data for a service+time window; `incident_investigation` prompt registered | 1. Drive the prompt with a scripted MCP client. | Consolidated answer includes a log excerpt (OpenSearch), metric/alarm data (CloudWatch), and a dashboard link (Kibana), each individually cited to its own source |
| TC-034 | FR-009/AC-002 | E2E | P1 | Same setup, but CloudWatch alarms fixture returns `status=empty` for the window | 1. Drive the same prompt. | Answer explicitly states the gap (e.g. "no CloudWatch alarms found in this window") instead of inferring or fabricating |

## Phase 3 — SQS/SNS (FR-010)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-035 | FR-010/AC-001 | integration | P1 | LocalStack (`sqs,sns`) with 1 queue + 1 topic seeded | 1. Call `sqs_get_queue_attributes`. 2. Call `sns_get_topic_attributes`. | `status=ok`; metadata includes `ApproximateNumberOfMessages` and name/ARN as citation |
| TC-036 | FR-010/AC-002 | integration | P1 | Nonexistent queue/topic name | 1. Call both attribute tools with a bad name. | `status=not_found` |
| TC-037 | FR-010/AC-003 | unit | P1 | `mcp-sqs-sns` `client.py` allowlist | 1. Attempt `SendMessage`/`DeleteMessage`/`Publish`. 2. Attempt `ReceiveMessage` (excluded — it mutates visibility timeout, a side effect). | `not_permitted` for all four; no code path calls `ReceiveMessage` |
| TC-038 | FR-010/AC-003 | integration | P2 | Full `mcp-sqs-sns` suite run against LocalStack | 1. Count messages on all LocalStack queues before the run. 2. Run the full suite. 3. Count again. | Message counts identical before/after |

## Phase 3 — Postgres+pgvector query server (FR-011)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-039 | FR-011/AC-001 | integration | P1 | pgvector (docker-compose) seeded with chunks relevant to a sample query | 1. Call `kb_semantic_search{query, top_k}`. | `status=ok`; top-N chunks returned, each with original `source_type` + original-source URI (not just `document_id`) as citation |
| TC-040 | FR-011/AC-001 | integration | P1 | `eval/recall_queries.yaml` (50 sample queries); compose Postgres populated via the ingest pipeline | 1. Run all 50 queries through `kb_semantic_search` with HNSW. 2. Re-run with `SET enable_indexscan=off` (brute-force ground truth). 3. Compare top-k sets. | recall ≥ 0.95 against brute-force baseline (ADR-0011 A3 gate, also the one concrete NFR-003-adjacent number this plan can assert today) |
| TC-041 | FR-011/AC-002 | integration | P1 | No embedded chunk meets `min_similarity` (best similarity, computed **without** filters, is below threshold) | 1. Call `kb_semantic_search` with a query far from any embedded content. | `status=empty`; `meta.warnings` states `best_similarity < min_similarity` — not presented as a confident low-relevance match |
| TC-042 | FR-011/AC-002 | integration | P1 | A chunk exists with high similarity, but `source_types`/`container`/`updated_after` filters exclude it | 1. Call `kb_semantic_search` with filters that exclude the otherwise-best match. | `status=empty`, **but** `meta.warnings` distinguishes "filters excluded N results" from "nothing is similar at all" — regression test for the HNSW post-filter false-negative that ADR-0011 A3 exists to prevent |
| TC-043 | FR-011/AC-003 | unit | P1 | `MCP_PGVECTOR_DSN` mistakenly set to the `mcp_ingest_rw` (write-capable) role's DSN | 1. Start `mcp-pgvector`. | Serve refused — startup check `has_table_privilege('kb.chunks','INSERT') = false` fails; this is the exact vulnerability ADR-0003 A1 patches |
| TC-044 | FR-011/AC-003 | integration | P2 | `mcp_query_ro` role, `BEGIN READ ONLY` transaction | 1. Attempt an INSERT/UPDATE/DELETE via the tool's parameterized SQL layer. | Rejected at the DB role level; no tool exposes such an operation to begin with (there is no `postgres_query(sql)` tool) |

## Phase 3 — Ingest/embedding pipeline (FR-012)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-045 | FR-012/AC-001 | integration | P1 | Compose Postgres; Confluence/GitLab connector fixtures with new/changed content | 1. Run `mcp-ingest run --source confluence,gitlab --mode incremental`. 2. Query the same content via `kb_semantic_search`. | Content is embedded and stored with source metadata, and is retrievable with a citation resolving to the original item (round-trip; also re-proves FR-011/AC-001) |
| TC-046 | FR-012/AC-002 | integration | P1 | One configured source (e.g. GitLab) simulated unreachable mid-run while Confluence succeeds | 1. Run `mcp-ingest run --source all`. | Run `status=partial`; failure recorded for GitLab; Confluence still commits; Confluence's previously stored embeddings are not corrupted; GitLab's checkpoint does **not** advance past the failed document's watermark (ADR-0012 A2) |
| TC-047 | FR-012/AC-002 | integration | P1 | `--mode full` reconcile where the crawl dies at 30% of `documents_seen` | 1. Run `mcp-ingest run --mode full` with the simulated failure. | Safety valve blocks tombstoning (`documents_seen < 0.8×` existing) → `IngestError{stage: reconcile}`; **no** document is tombstoned |
| TC-048 | FR-012/AC-002 | integration | P2 | A document fails `MCP_INGEST_MAX_DOC_RETRIES` times | 1. Inspect `kb.ingest_failures`. 2. Run `mcp-ingest run --retry-failed`. | Failing doc appears in `ingest_failures` with `attempts`/`stage`/`code`; `--retry-failed` re-ingests it and removes the row on success |
| TC-049 | FR-012/AC-003 | integration | P1 | Previously ingested content changed at the source (new `content_hash`) | 1. Run `mcp-ingest run` once. 2. Change the source content. 3. Run it again. | Stored embedding/metadata is updated/replaced, not duplicated (`UNIQUE(source_type, source_id)`); two runs on **unchanged** content also produce no duplicates |
| TC-050 | FR-012/AC-003 | integration | P2 | `content_hash` unchanged but `title`/`source_uri` changed (e.g. a Confluence page renamed or moved to another space) | 1. Re-run ingest for that document. | Chunk+embed is skipped (hash match), but `title`/`source_uri`/`container`/`author`/`last_seen_*` are updated regardless — otherwise citation goes stale (ADR-0012 A4) |
| TC-051 | FR-012/AC-001 | integration | P1 | Source content containing a secret-looking token (e.g. an `.env`-style line, an API-key pattern) | 1. Run ingest over that content. 2. Query `kb_semantic_search` for it. | The secret never appears in `kb.chunks` nor in the search result; content blocked by deny-glob shows up in `kb.ingest_failures{stage: redact, code: blocked_by_policy}` instead (R4 regression) |

## Phase 3 — Journey 3: semantic + messaging synthesis (FR-013)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-052 | FR-013/AC-001 | E2E | P1 | `mcp-pgvector` + `mcp-sqs-sns` served over stdio; seeded embedded chunks + a seeded queue; `semantic_synthesis` prompt registered | 1. Drive the prompt with a scripted MCP client. | Answer cites the **original** source(s) behind each matched embedding (original URL, not `document_id`) and the queue/topic reference when SQS/SNS data was used |
| TC-053 | FR-013/AC-002 | E2E | P1 | pgvector query configured to return `status=empty` | 1. Drive the same prompt with an off-topic question. | Answer explicitly states no relevant indexed data was found; does not fabricate an answer |

## Cross-cutting — read-only enforcement, all 9 servers (FR-014)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-054 | FR-014/AC-001 | unit | P1 | `tools.snapshot.json` for each of the 9 packages | 1. Run `assert_readonly_tool_surface` for each snapshot against `api-contract.yaml`. | Zero tools with a write/update/delete side effect. Default registered surface is **48 tools**; **49** with `MCP_OPENSEARCH_ALLOW_DSL=true` (assert both counts; `opensearch_search_dsl` absent by default — ADR-0008 A7); every operation carries `x-readonly: true`/`x-side-effects: none` |
| TC-055 | FR-014/AC-001 | unit | P1 | Transport-assertion fixture active for every unit test (`respx`) | 1. Run the full unit suite for all 9 packages. | Every outbound request is `GET`/`HEAD` except explicit allowlisted `(host, method, path)` tuples; any `POST`/`PUT`/`DELETE` fails the test immediately |
| TC-056 | FR-014/AC-002 | E2E | P1 | Each of the 9 servers served over real stdio | 1. Send a JSON-RPC `tools/call` for a fabricated/nonexistent "write" tool name (e.g. `confluence_delete_page`) to each server in turn. | MCP SDK returns the JSON-RPC "unknown tool" **protocol** error (not an `ErrorEnvelope` — per `api-contract.yaml` `info.description`, this AC lives at the protocol layer, not the contract-schema layer); no source data changes for any of the 9 |
| TC-057 | FR-014/AC-002 | unit | P2 | A request targeting an operation that **does** exist but is outside the allowlist (e.g. an OpenSearch body containing `script`) | 1. Call the tool with the forbidden construct. | `error.code=not_permitted` — distinct mechanism from TC-056's protocol-layer case; the contract explicitly calls out that these are two different things and a tester should not conflate them |

## Cross-cutting — traceability / no-hallucination (FR-015)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-058 | FR-015/AC-001 | unit | P1 | `mcp_common.envelope` invariant | 1. Attempt to construct `ToolResult(status=ok, citations=[])`. | Construction fails validation; golden-file render snapshot test covers all 4 status branches × a representative list-tool and detail-tool |
| TC-059 | FR-015/AC-001 | integration | P1 | At least one real tool call per server returning `status=ok` | 1. Inspect the text rendering for each. | Response includes a resolvable citation (link/id) for every distinct source used, `[n]` markers matching `citation_ref` |
| TC-060 | FR-015/AC-002 | unit | P1 | All relevant tool calls in a scenario return `empty`/`not_found` | 1. Render the text output. | Response explicitly states no matching data was found across the queried sources; the `Nguồn:`/"Sources" section is entirely absent, never fabricated |

## Non-functional requirements

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-061 | FR-014/AC-001, FR-014/AC-002 (NFR-001) | integration | P1 | Full `test_tools_readonly.py` suite across all 9 packages + `mcp-ingest` connector methods | 1. Run the suite for every package before each phase sign-off. | 100% of attempted mutating operations across all 9 servers are rejected; 0 exceptions |
| TC-062 | FR-001/AC-002, FR-002/AC-002 (NFR-002) | unit | P1 | Simulated unreachable endpoint (`respx` `ConnectError`) for each HTTP-based server | 1. Call any tool against the dead endpoint. | `error.code ∈ {upstream_timeout, upstream_unavailable}`; exactly 2 attempts (1 retry); total elapsed ≈ 21s and strictly **< 25s** deadline. `# THRESHOLD TBD (Open question 4 — PO has not confirmed the final bound; interim assertion uses the numbers architecture.md/ADR-0006 A2 already committed: connect 3s / read 7s / 2 attempts / 1s backoff ⇒ 2×(3+7)+1 = 21s < 25s)` |
| TC-063 | NFR-002 | integration | P1 | `ThreadPoolExecutor(max_workers=4)` saturated with 4 hung synchronous SDK calls (CloudWatch/Kafka) | 1. Issue a 5th tool call while all 4 workers are blocked. | The 5th call returns `upstream_unavailable` immediately — does not wait for a free worker (R16 regression) |
| TC-064 | NFR-002 | unit | P2 | Kafka `socket.timeout.ms`/`metadata.request.timeout.ms=8000`, Redis connect 2s/read 5s, Postgres `connect_timeout=3`/`statement_timeout=15s` | 1. Simulate an unreachable broker/Redis/Postgres for each. | Each surfaces a distinguishable timeout error within its configured bound; none hangs indefinitely |
| TC-065 | NFR-003 | integration | P2 | `eval/questions.yaml`, ≥10 questions per phase with `expected_sources`/`expected_behavior` | 1. Run `scripts/run_eval.py` against the seeded corpus (automated harness). 2. **Manual review step:** inspect each answer against expectations and record the citation-rate. | Proportion of answers with a valid, resolvable citation when tool data was used is recorded and reported. The harness run is automated (integration level); the final judgement of "answer correct + correctly cited" is a manual review captured in the regression report. `# THRESHOLD TBD (Open question 1 — exact pass threshold to be set by PO after Phase 1 launch; this run only produces the measured proportion for that decision, it does not itself gate PASS/FAIL)` |
| TC-066 | FR-011/AC-001 (NFR-003) | integration | P1 | Same setup as TC-040 | 1. See TC-040. | recall ≥ 0.95 — the one concrete NFR-003-adjacent number this plan can assert today, independent of the PO threshold pending in TC-065 |
| TC-067 | NFR-004 | integration | P2 | `mcp-ingest run` completed at least once | 1. Run `mcp-ingest status --json`. | Output matches the `IngestStatusRow` schema; `staleness_hours` computed correctly per source. `# THRESHOLD TBD (Open question 5 — acceptable freshness bound not yet set by PO/SA; architecture.md records SA's *candidate* proposal — incremental hourly + full reconcile at 03:00 + staleness ≤4h during business hours — as not yet confirmed. This test only asserts the number is reported correctly, not that it passes a bound)` |
| TC-068 | NFR-004 | integration | P2 | Populated DB vs. an empty DB | 1. Call `kb_list_sources` in both states. | Populated → `meta.data_freshness` with `last_ingested_at` + `staleness_hours` + doc/chunk counts; empty DB → `status=empty`, not an error |
| TC-069 | NFR-005 | unit | P1 | Stdout guard active in every unit test, every package; a `print()` deliberately injected into a tool body for this test only | 1. Call any tool while the injected `print()` fires. | Test fails immediately on any byte written to stdout outside JSON-RPC framing; all structured logs go to stderr only |
| TC-070 | NFR-005 | E2E | P2 | `mcp-common config-emit --server <name>` output pasted into `claude_desktop_config.json`; each server spawnable over real stdio | 1. Register each of the 9 servers in Claude Desktop/Code (or an equivalent stdio MCP client harness). 2. Spawn each over stdio and issue one `tools/call` per server. | Each server appears in the MCP tool list and responds over the stdio/JSON-RPC boundary, with no separate UI/web service required. (E2E = real server process over stdio, this project's E2E definition; the Claude Desktop registration is the manual-equivalent surface captured in the regression report.) |

## Open-decision markers (Gate B / spikes — kept distinct for `qa-verify` re-run)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-071 | FR-012/AC-001, FR-012/AC-003 | integration | P2 | `# MODEL TBD (spike S2 / ADR-0010 — bge-m3 vs multilingual-e5-large; both candidates are 1024d so no schema change is needed either way)` | 1. Run `scripts/bakeoff_embedding.py` comparing both candidate models on the NFR-003 question set. | Recall@k / latency / RAM / load-time comparison table produced; whichever model S2 ultimately selects, TC-039/TC-040/TC-066 must be re-run against the final choice — no test rewrite needed, only re-execution |
| TC-072 | FR-012/AC-001 (ADR-0016 Part 2, Open question 3) | integration | P3 | **CLOSED — NOT APPLICABLE.** Gate B/ADR-0016 A1 chose a team-only corpus; T-067 (RBAC filtering) is closed. | **Not executed** — kept as a tombstone row so ids stay stable. Replaced by TC-073 (reject/purge on non-team visibility). | **N/A (closed).** No RBAC per-user filtering in v1; the active case is TC-073 (team-only default-deny + purge-on-relabel). Level/Priority are placeholders to keep the id valid; this TC is not run. |
| TC-073 | FR-012/AC-001 | integration | P1 | Team-only corpus is the **decided** policy (ADR-0016 A1–A3, default-deny S5); compose Postgres | 1. Run `mcp-ingest run` against a fixture containing a document whose inferred `visibility != 'team'`. 2. Ingest a document as `team` (chunks exist in `kb.chunks`), then change the source so its label becomes `restricted` (or its space/project leaves the allowlist) and re-run ingest. 3. Query `kb_semantic_search` for its content. | Step 1: document rejected and recorded in `kb.ingest_failures{code: blocked_by_policy}`; never reaches `kb.chunks`. Step 2 (purge-on-relabel): its chunks are removed and the document is tombstoned in the same transaction (ADR-0016 A1). Step 3: no chunk of it is returned |
| TC-074 | FR-012/AC-003 | integration | P2 | `# RETENTION DEFAULT TBD (mcp-ingest prune has no implicit default; api-contract.yaml requires at least one of tombstoned/older_than_days to be passed explicitly)` | 1. Run `mcp-ingest prune` with no flags. 2. Run `mcp-ingest prune --tombstoned --older-than 30d --dry-run`. | Step 1 is rejected by the request schema (`anyOf: [tombstoned, older_than_days]` required); step 2 reports `documents_deleted`/`chunks_deleted` without modifying data (`dry_run` default `true`) |
| TC-075 | FR-008/AC-001 | integration | P1 | `redis:7` (docker-compose) with `mcp_ro` ACL from `infra/redis/users.acl` including `+select` (ADR-0008 A5); key pre-seeded in `db=1` | 1. Call `redis_get_key{key, db: 1}`. 2. Attempt `SELECT` as a tool-level command. | Step 1: `status=ok`, value returned with key citation (no `forbidden`/ACL error). Step 2: `not_permitted` — `SELECT` is not in the tool command allowlist; startup ACL check (write-category) still passes with `+select` granted |
| TC-076 | FR-004/AC-001, FR-004/AC-002 | unit | P1 | `MCP_OPENSEARCH_ALLOW_DSL=true`; mocked `opensearch-py` | 1. Call `opensearch_search_dsl{limit: 10, body.size: 50}`. 2. Call with `from: 950, size: 100` (from+size = 1050 > 1000). 3. Call with `from: 901`. 4. Call with `from: 500, size: 10, limit: 10`. | Step 1: `size` clamped to `limit` (10) with a `meta.warnings` entry; `status=ok`. Step 2: rejected with `error.code=invalid_input` (`details.field=body.from`) and no upstream call made. Step 3: rejected (`from` ∈ 0..900). Step 4: accepted (`from` not forced ≤ `limit`). **Note:** the `ErrorCode` enum in `api-contract.yaml` lists `invalid_input` and the runtime returns `invalid_input` (verified in qa-verify run 1); if the runtime ever differs from the enum, triage as `contract`. The `search_after` hint of the `from + size > 1000` rule is unreachable through the tool schema (`from` ≤ 900 and `size` ≤ 100 already cap the sum at 1000) — see regression-report.md, spec observation S-1 |
| TC-077 | FR-011/AC-001 (NFR-004) | integration | P2 | Compose Postgres: source A has live documents; source B has only tombstoned documents (or none) | 1. Call `kb_list_sources`. | Source A appears in `items` with `uri` = origin of a live document's `source_uri` (never `null`, invariant 6); source B is **absent from `items`** and named in `meta.warnings` |

## AC → TC traceability summary

All 37 AC id from `requirements.md` map to at least one TC above:

| FR | AC ids | TC ids |
|---|---|---|
| FR-001 | AC-001, AC-002, AC-003 | TC-001, TC-002, TC-003, TC-004, TC-005, TC-006 |
| FR-002 | AC-001, AC-002, AC-003 | TC-007, TC-008, TC-009, TC-010, TC-011, TC-012, TC-013 |
| FR-003 | AC-001, AC-002 | TC-014, TC-015 |
| FR-004 | AC-001, AC-002 | TC-016, TC-017, TC-018, TC-076 |
| FR-005 | AC-001, AC-002 | TC-019, TC-020 |
| FR-006 | AC-001, AC-002 | TC-021, TC-022, TC-023 |
| FR-007 | AC-001, AC-002, AC-003 | TC-024, TC-025, TC-026, TC-027, TC-028 |
| FR-008 | AC-001, AC-002, AC-003 | TC-029, TC-030, TC-031, TC-032, TC-075 |
| FR-009 | AC-001, AC-002 | TC-033, TC-034 |
| FR-010 | AC-001, AC-002, AC-003 | TC-035, TC-036, TC-037, TC-038 |
| FR-011 | AC-001, AC-002, AC-003 | TC-039, TC-040, TC-041, TC-042, TC-043, TC-044, TC-066, TC-071, TC-077 |
| FR-012 | AC-001, AC-002, AC-003 | TC-045, TC-046, TC-047, TC-048, TC-049, TC-050, TC-051, TC-071, TC-073, TC-074 |
| FR-013 | AC-001, AC-002 | TC-052, TC-053 |
| FR-014 | AC-001, AC-002 | TC-054, TC-055, TC-056, TC-057, TC-061 |
| FR-015 | AC-001, AC-002 | TC-058, TC-059, TC-060 |
| NFR-001 | — | TC-061 |
| NFR-002 | — | TC-062, TC-063, TC-064 |
| NFR-003 | — | TC-065, TC-066 |
| NFR-004 | — | TC-067, TC-068, TC-077 |
| NFR-005 | — | TC-069, TC-070 |

`uncovered_ac: []` — 37/37 base AC id covered.

---

# CHG-001 Option C + B4 Grounding — Company Knowledge tier (TC-078…TC-110)

> **Extension (CHG-001 Option C + B4 Grounding, approved Gate 1 / CTO D-004).** Base TC-001…TC-077 are
> **unchanged and not re-run as part of this slice**; the rows below are **new** test cases numbered
> TC-078+ that cover the **26 new AC id** across **FR-016…FR-022** (requirements.md) and the 13 new tools +
> grounding envelope + two choke points (api-contract.yaml, ADR-0018 GT-1..GT-7, ADR-0016 default-deny,
> implementation-plan.md T-087…T-110 qa_notes). Every new AC maps to ≥1 TC that cites its exact
> `FR-xxx/AC-xxx` id; GT-1..GT-7 of ADR-0018 §6 map to FR-021 per the lead's qa_notes
> (GT-1→AC-001, GT-2→AC-002, GT-3→AC-004, GT-4→AC-003, GT-5+GT-7→AC-006, GT-6→AC-005).
>
> **Threshold discipline (NFR-010 / L-002 / E-004).** The FACT↔LOW_CONFIDENCE thresholds `τ_fact`/`τ_low`
> are **TBD/UNVERIFIED** — blocked by HuggingFace egress (NFR-003 remains UNVERIFIED). Every TC that asserts
> a grounding **verdict invariant** (no-evidence⇒UNKNOWN, source⇒FACT, conflict⇒CONFLICT, missing-provenance⇒
> never-FACT, single choke point, provenance-preserved, confidence-is-a-labelled-proxy) is **independent of τ
> and runs now** — these are GT-1..GT-7 and the permission adversarial tests, the highest-value cases.
> Any TC whose numeric pass bound depends on **recall/τ measured on a real golden-set** carries a
> `# THRESHOLD TBD` marker and is **conditional** — it reports its measured value and asserts only the
> `calibration_status: uncalibrated` contract, pending HF egress; it never invents a number (TC-108-class).

## CHG-001 · FR-016 Hybrid grounded search (Company Knowledge)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-078 | FR-016/AC-001 | integration | P1 | compose Postgres seeded with team-visibility corpus relevant to the query; `mcp-knowledge` served; reranker weights present (`HF_HUB_OFFLINE=1`, local) | 1. Call `search_company_knowledge{query}` with a query that has indexed evidence. | `status=ok`; `claims[]` returned, each with ≥1 citation resolvable to the original source (`source_uri`, not `document_id`); `grounding_summary` counts the verdicts (`fact`/`low_confidence`/`unknown`/`conflict`); hybrid path (tsvector ∪ pgvector ∪ metadata, RRF k=60) exercised |
| TC-079 | FR-016/AC-002 | integration | P1 | Corpus has **no** candidate chunk relevant to an off-topic query | 1. Call `search_company_knowledge{query: off-topic}`. | `status=empty` **or** `status=insufficient_evidence`; `claims[]` carries **no** fabricated claim (no `FACT`); not an `ErrorEnvelope`, not a low-relevance match presented as confident |
| TC-080 | FR-016/AC-003 | integration | P1 | reranker weights deliberately **absent/unloadable**; RRF-only fallback flag reachable | 1. Call `search_company_knowledge{query}` with evidence present. | System falls back to **RRF-only** (does not raise); `grounding_summary.reranker == "disabled"` is set explicitly; the degraded ranking is **not** presented as full-quality (L-002 fallback transparency) |
| TC-081 | FR-016/AC-004 | integration | P1 | `HF_HUB_OFFLINE=1` set; `mcp-knowledge` DSN is the `mcp_query_ro` role; network egress monitored (socket assertion harness) | 1. Run `search_company_knowledge` end to end. 2. Assert no outbound socket opened during retrieval/rerank/grounding. 3. Attempt any write via the role. | Served over stdio in-process; `HF_HUB_OFFLINE=1` in effect; **zero outbound sockets** during the call; the `mcp_query_ro` role rejects every INSERT/UPDATE/DELETE at the DB level (EB-004/EB-005 regression) |

## CHG-001 · FR-017 Jira as source #10 (Live Jira MCP, read-only)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-082 | FR-017/AC-001 | integration | P1 | `respx` fixtures for **both** Jira flavors: Cloud (`/rest/api/3`, `nextPageToken`) and Server/DC (`/rest/api/2`, `startAt`), each returning a matching issue / project / sprint the caller can read | 1. Call `jira_search_issues`, `jira_get_issue`, `jira_list_projects`, `jira_get_sprint`, `jira_list_board_sprints` with matching identifiers, once per flavor. | `status=ok`; each item carries issue key / project key / sprint id and a Jira **URL** usable as citation; the Cloud/Server cursor split is opaque (same tool behavior both flavors) |
| TC-083 | FR-017/AC-002 | integration | P1 | Nonexistent issue key / board / sprint id (`respx` 404); a list query with no matches | 1. Call `jira_get_issue{key: "ZZZ-9999"}`, `jira_get_sprint{sprint_id: unknown}`, `jira_list_board_sprints{board_id: unknown}`. 2. Call `jira_list_projects` / `jira_search_issues` with a no-match filter. | Specific unknown identifier → `status=not_found`; list with no match → `status=empty` (`citations: []`); never a fabricated issue/sprint |
| TC-084 | FR-017/AC-003 | E2E | P1 | `mcp-jira` served over real stdio | 1. Send JSON-RPC `tools/call` for fabricated Jira **write** tool names not in `tools/list`: `jira_create_issue`, `jira_transition_issue`, `jira_add_comment`. 2. Inspect `client.py` `ALLOWED_OPERATIONS` for any non-GET verb. | Each write tool → MCP SDK **JSON-RPC "unknown tool"** protocol error (not `ErrorEnvelope`, FR-014/AC-002 at protocol layer); `respx` asserts **zero** non-GET requests reached Jira; no data in Jira modified (BR-006 read-only adversarial) |
| TC-085 | FR-017/AC-004 | integration | P1 | compose Postgres; Jira connector fixture; a Jira issue ingested as a live document, then it disappears at the source; safety-valve condition (`documents_seen ≥ 0.8× existing`) satisfied | 1. Run `mcp-ingest run --source jira --mode full`. 2. Query `search_company_knowledge` for its content. 3. Re-run ingest on unchanged content. | The disappeared issue's document is tombstoned (`deleted_at` set) and its chunks removed; step 2 returns no claim backed by it; step 3 produces **no duplicate** (`UNIQUE(source_type,source_id)`); watermark boundary inclusive `>=` |

## CHG-001 · FR-018 Live-vs-Knowledge decision (freshness, conflict, source authority)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-086 | FR-018/AC-001 | integration | P1 | A knowledge snapshot and a live Jira value that **agree** for a claim about an entity | 1. Call `get_jira_context{subject}` (and `search_company_knowledge` with `include_live=true`). | The claim is returned as a **single grounded claim** (not a conflict) with provenance from **both** sides; `meta.data_freshness`/staleness indicator present |
| TC-087 | FR-018/AC-002 | integration | P1 | Snapshot and live Jira value **differ** for the same claim; `0008_source_authority` seeded (current-work-status→Jira) | 1. Call `get_jira_context{subject}` / `search_company_knowledge{include_live:true}`. | Claim verdict `CONFLICT`; **both** positions appear with full provenance (`source_version`, `updated_time`); `authority_note` reflects the **configured** source-authority for that fact type; **nothing silently merged or chosen**; no authority hardcoded in the prompt (GT-4 cross-ref FR-021/AC-003) |
| TC-088 | FR-018/AC-003 | integration | P2 | A fact whose `updated_time` is older than its configured `freshness_horizon` | 1. Compute confidence for that claim via `search_company_knowledge`. | The freshness **factor is reduced** (claim downweighted); the claim is **not** made false solely by age; staleness is observable in `meta`. `# THRESHOLD TBD (NFR-010 — the exact downweight curve/τ is calibrated on golden-set after HF egress; this TC asserts the direction (reduced, not zeroed, not false) and that staleness is reported, not a numeric bound)` |

## CHG-001 · FR-019 Server-side permission enforcement before context assembly (adversarial, L-001)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-089 | FR-019/AC-001 | integration | P1 | Corpus with documents the caller **is** permitted to see (`document_permissions`/`visibility` grants present) | 1. Query `search_company_knowledge` as that caller. | Only permitted documents are retrieved, ranked, and cited; permitted content appears normally |
| TC-090 | FR-019/AC-002 | integration | P1 | **Adversarial.** Corpus contains a `restricted` document whose content **matches** the query but the caller has **no grant** (default-deny); the exact query is crafted to retrieve it | 1. Issue that exact query via `search_company_knowledge` as the un-granted caller. 2. Inspect candidate set, context-pack claims, and citations. | The restricted document does **not** appear as a candidate, is **not** in the context-pack, and is **not** cited as evidence — it is excluded **before** context assembly (default-deny at choke point #1). Test **fails if any leak** into candidate/pack/citation (ADR-0016 A1/A2, L-001 adversarial) |
| TC-091 | FR-019/AC-003 | unit | P1 | Source of all code paths that reach context assembly | 1. Structural test: enumerate every path producing a context-pack. 2. Assert permission filtering is applied at exactly **one** `enforce_permission()` choke point every path passes through. 3. Assert permission runs **strictly before** the grounding gate. | Exactly **one** permission choke point (#1), **no bypass**; `enforce_permission` executes **before** the grounding gate (#2) — the two choke points are **separate** and ordered (not merged, not duplicated) |

## CHG-001 · FR-020 Document versioning, entities/relationships, knowledge summaries

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-092 | FR-020/AC-001 | integration | P1 | A document with version history; an entity with relationships; a seeded knowledge summary | 1. Call `get_document_version{document_id}`. 2. Call `find_related_knowledge{entity, max_depth: 2}`. 3. Call `get_service`/`get_repository`/`get_knowledge_summary`. | `get_document_version` returns versions (`current`/`superseded`) with `source_version`/`author`/`source_updated_at` + citation; `find_related_knowledge` returns edges up to depth, each with `rel_type`, `depth`, citation; service/repo/summary resolve with provenance |
| TC-093 | FR-020/AC-002 | integration | P1 | A nonexistent/tombstoned `document_id`/entity; an existing entity with **no** edges and no summary | 1. Call each FR-020 tool with the missing id. 2. Call `find_related_knowledge`/`get_knowledge_summary` on the edge-less/summary-less entity. | Missing/tombstoned id → `status=not_found`; existing-but-empty entity → `status=empty`; **never** a fabricated version/edge/summary |
| TC-094 | FR-020/AC-003 | integration | P1 | `kb.relationships` seeded with a **cycle** and a high-fan-out node | 1. Call `find_related_knowledge{entity, max_depth: 3}` on the cyclic/high-fan-out graph. | Traversal is **bounded to depth ≤ 3** with cycle detection and a fan-out LIMIT; it **terminates** (no runaway); recursive CTE returns a bounded edge set |

## CHG-001 · FR-021 B4 Grounding verdict + per-claim provenance (GT-1..GT-7, adversarial)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-095 | FR-021/AC-001 (GT-1) | integration | P1 | **Adversarial.** Corpus has **no** evidence for the asked proposition | 1. Call `search_company_knowledge` for that proposition. | **No** claim is `FACT`; result is `UNKNOWN` / `status=insufficient_evidence`; the **fixed** message "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này." appears; server does **not** invent an answer. Runs **now** (independent of τ) |
| TC-096 | FR-021/AC-002 (GT-2) | integration | P1 | Corpus **has** evidence for the proposition (6 provenance fields present) | 1. Call `search_company_knowledge`. 2. Resolve `claim.provenance.evidence{document_id,chunk_id}` via `kb_get_document`. | Claim is `FACT` carrying all **6** provenance fields (`source`, `source_version`, `owner`, `updated_time`, `confidence`, `evidence`); `evidence` **resolves** via `kb_get_document` to **exactly** that chunk (fake/un-openable evidence = fail). Runs now |
| TC-097 | FR-021/AC-003 (GT-4) | integration | P1 | Two sources assert **different** values for the same claim | 1. Call `search_company_knowledge`. | Verdict `CONFLICT`; **both** positions appear with full provenance; **nothing** silently merged or chosen; `authority_note` reflects the source-authority config. Runs now (cross-ref FR-018/AC-002) |
| TC-098 | FR-021/AC-004 (GT-3) | integration | P1 | A candidate claim deliberately **missing** a required evidence field (`evidence` or `updated_time`) | 1. Feed it through the grounding gate. | It is **never** `FACT` (becomes `UNKNOWN`/`LOW_CONFIDENCE`); it is **not** passed through ungraded. Runs now (independent of τ) |
| TC-099 | FR-021/AC-005 (GT-6) | integration | P1 | Any returned claim; `calibration_status` observable | 1. Inspect every `claim.confidence` and `grounding_summary`. 2. Attempt to make a high-`confidence` no-evidence claim become `FACT`. | `confidence` always carries `confidence_basis == "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)"`; `grounding_summary.calibration_status == "uncalibrated"` (τ = TBD, NFR-010); **no path** turns a high-`confidence` **no-evidence** claim into `FACT`. `# THRESHOLD TBD (NFR-010/L-002 — τ_fact/τ_low measured on golden-set after HF egress; this TC asserts the labelling + uncalibrated contract + the no-evidence⇒not-FACT invariant, NOT a τ value)` |
| TC-100 | FR-021/AC-006 (GT-5+GT-7) | unit | P1 | Source of all code paths producing a context-pack; a context-compression step before the gate | 1. **GT-5 structural:** assert every claim leaving the server passed the **single** grounding gate (no bypass). 2. **GT-7:** run compression, assert each claim still carries all 6 provenance fields entering the gate. | Exactly **one** grounding gate (#2), no bypass path (NFR-008); context-compression **preserves** each claim's provenance ("never compress away provenance"). Runs now. Note: this is the gate choke point, **distinct** from the permission choke point of TC-091 |

## CHG-001 · FR-022 Gateway-boundary in-process (routing, auth-context, rate-limit, audit; stdio kept)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-101 | FR-022/AC-001 | integration | P1 | `mcp_gateway` in-process fronting Knowledge/Live servers; stderr capture | 1. Invoke a valid Knowledge/Live tool through the gateway. | Gateway routes it to the correct server, writes an **append-only audit entry (to stderr)** with request identity, and returns the tool result |
| TC-102 | FR-022/AC-002 | integration | P1 | Configured token-bucket rate-limit | 1. Exceed the configured rate-limit with rapid calls. | Excess calls rejected with `error.code=rate_limited` carrying `retry_after_s` (not an upstream overload); the rejection is audited |
| TC-103 | FR-022/AC-003 | integration | P1 | Gateway process running; port/socket inspection + stdout guard active | 1. Inspect the process for any listening socket. 2. Assert all gateway logs/audit go to stderr. | The gateway has opened **no** network port (stdio only); **zero** listening sockets; all audit/logs on stderr, **never** stdout (EB-005/BR-012 regression, NFR-012) |

## CHG-001 · Cross-cutting — read-only surface & stdio for the 2 new servers (FR-014 regression on new surface)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-104 | FR-016/AC-004, FR-017/AC-003 (NFR-006) | unit | P1 | `tools.snapshot.json` for `mcp-knowledge` (8 tools) and `mcp-jira` (5 tools) | 1. Run `assert_readonly_tool_surface` for both snapshots against `api-contract.yaml`. 2. Assert every new operation carries `x-readonly: true` / `x-side-effects: none`. 3. Assert the full-platform surface count: **61 tools default, 62 with `MCP_OPENSEARCH_ALLOW_DSL=true`** (48 base + 13 new = 8 Knowledge + 5 Jira). | Zero write/update/delete tools among the 13 new tools; both counts (61/62) assert; all 13 carry the read-only markers (BR-006/NFR-006) |
| TC-105 | FR-014/AC-002 (NFR-006) | E2E | P1 | `mcp-knowledge` and `mcp-jira` each served over real stdio (JSON-RPC) | 1. For each server, send a `tools/call` for a fabricated non-existent "write" tool name (e.g. `knowledge_delete_document`, `jira_create_issue`). | MCP SDK returns the JSON-RPC **"unknown tool" protocol error** (not an `ErrorEnvelope`, per api-contract.yaml FR-014/AC-002 at the protocol layer); no source data changes. Read-only JSON-RPC surface holds for both new servers |

## CHG-001 · Conditional / UNVERIFIED (τ & recall blocked by HF egress — NFR-003/NFR-010, L-002)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-106 | FR-016/AC-001 (NFR-003), FR-021/AC-005 (NFR-010) | integration | P2 | `eval/grounding/` harness; **real golden-set + measured embedding/rerank require HF egress, currently blocked (403)** | 1. When HF egress is cleared: run the golden-set, measure grounding recall/precision and calibrate `τ_fact`/`τ_low`. 2. Until then: run with the deterministic fake provider and the starter constants. | **CONDITIONAL / TBD.** `# THRESHOLD TBD (NFR-010/NFR-003, L-002/E-004 — τ_fact/τ_low and real recall are meaningful only on a measured golden-set, blocked by HF egress; starter τ_fact=0.6/τ_low=0.3 are for INFRASTRUCTURE tests only and are NOT claimed correct)`. Until egress: harness runs on fake provider, envelope reports `calibration_status: uncalibrated`, the TC reports measured values **without** gating PASS/FAIL on an unset number; it does **not** invent a bound. On HF-cleared re-run, τ is recorded and ADR-0018 moves to *accepted* |

## CHG-001 · AC → TC traceability summary

All **26 new AC id** from `requirements.md` (FR-016…FR-022) map to ≥1 new TC above:

| FR | AC ids | TC ids |
|---|---|---|
| FR-016 | AC-001, AC-002, AC-003, AC-004 | TC-078, TC-079, TC-080, TC-081, TC-104, TC-106 |
| FR-017 | AC-001, AC-002, AC-003, AC-004 | TC-082, TC-083, TC-084, TC-085, TC-104 |
| FR-018 | AC-001, AC-002, AC-003 | TC-086, TC-087, TC-088 |
| FR-019 | AC-001, AC-002, AC-003 | TC-089, TC-090, TC-091 |
| FR-020 | AC-001, AC-002, AC-003 | TC-092, TC-093, TC-094 |
| FR-021 | AC-001, AC-002, AC-003, AC-004, AC-005, AC-006 | TC-095, TC-096, TC-097, TC-098, TC-099, TC-100, TC-106 |
| FR-022 | AC-001, AC-002, AC-003 | TC-101, TC-102, TC-103 |
| NFR-006 | — | TC-081, TC-084, TC-104, TC-105 |
| NFR-007 | — | TC-090, TC-091 |
| NFR-008 | — | TC-091, TC-100 |
| NFR-009 | — | TC-095, TC-096, TC-097, TC-098 |
| NFR-010 | — | TC-088, TC-099, TC-106 (all `# THRESHOLD TBD`) |
| NFR-011 | — | TC-080, TC-081, TC-103 |
| NFR-012 | — | TC-103 |

**GT (ADR-0018 §6) → TC map (adversarial, run now, independent of τ):**
GT-1 → TC-095 · GT-2 → TC-096 · GT-3 → TC-098 · GT-4 → TC-097 · GT-5 → TC-100 (structural) · GT-6 → TC-099 · GT-7 → TC-100 (compression).

**Highest-priority adversarial / safety-gate TCs (run now, no τ dependency):**
TC-090 (restricted doc never a candidate/pack/citation), TC-095 (no-source→UNKNOWN), TC-096 (source→FACT, evidence resolves), TC-097 (conflict→CONFLICT), TC-098 (missing provenance never FACT), TC-091 (permission single choke point, before gate), TC-100 (grounding single gate + provenance preserved), TC-084/TC-105 (read-only JSON-RPC surface, no write tools), TC-103 (gateway no network port).

**Conditional / UNVERIFIED (defer numeric bound to HF-egress-cleared re-run):** TC-088, TC-099, TC-106 — all carry `# THRESHOLD TBD`; they report measured values and assert only `calibration_status: uncalibrated` + verdict-direction invariants, never an invented τ/recall number.

`new_uncovered_ac: []` — 26/26 new AC id (FR-016…FR-022) covered; 63/63 total AC id (37 base + 26 new) covered.

---

# CHG-003 · Real ingestion + real egress (CLI-driven, 9-source runbook, Confluence first) — test-cases extension

> Appends **TC-111…TC-131** — continues numbering, no renumber of TC-001…TC-110.
> Grounded in `requirements.md` FR-023…FR-027 (17 AC) + NFR-013/NFR-014, ADR-0023 §6a–§6e, and
> `implementation-plan.md` CHG-003 (T-111…T-123). The **four ADR-0023 §6e adversarial/invariant tests** are
> **TC-113** (#1 egress default-deny), **TC-114** (#2 allow-list honoured / unlisted-host-refused),
> **TC-115** (#3 token-never-leaked, forced-error, scrub both ways — E-003), **TC-125** (#4 server
> read-only + stdio unchanged, 0 ports). All CI/dev TCs run against **`respx` fixtures + a fake read-only
> token + a model stub**; TCs tagged **`@live`** (TC-121, TC-128-live note, TC-131) need the CEO's real
> token + VPN + online egress and are **NOT part of `make ci`** (gated by `MCP_INGEST_ALLOW_LIVE_EGRESS=true`).
> Lessons applied: L-001 (single choke point + adversarial input), L-002 (no invented NFR-003 number),
> L-003 (path-tolerant fixture/contract resolution). Guards recurring classes **E-003** (token leak) and
> **E-007** (permission choke point).

## CHG-003 · FR-024 Default-deny egress with a source-host allow-list (incl. §6e#1, #2 adversarial)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-111 | FR-024/AC-001 | integration | P1 | Egress allow-list configured with the Confluence Cloud host; `respx` fixture for `tnexwm.atlassian.net`; fake read-only token | 1. Run the ingest pull path toward the configured host `tnexwm.atlassian.net`. | The connection is **permitted** and the pull proceeds through `check_egress`; the configured host is reached (NFR-013 allow-side) |
| TC-112 | FR-024/AC-003 (NFR-013) | unit | P1 | Source of all code paths that make an outbound call on the ingest-pull / model-download path | 1. **Structural (L-001):** enumerate every outbound-call site on the pull/model path. 2. Assert each passes through exactly **one** `check_egress` choke point. 3. Assert there is **no** bypass path that reaches the network without the guard. | Exactly **one** `check_egress` structural choke point on the egress path; **no bypass** (same single-gate shape as CHG-001 GT-5/TC-100); egress is a property of the `mcp-ingest` pull + model download, not of any server |
| TC-113 | FR-024/AC-002 (NFR-013) | integration | P1 | **Adversarial (ADR-0023 §6e#1, L-001).** Allow-list set to the configured source host(s) only; a request is aimed at a host **not** on the list (`evil.example.com` / an arbitrary external domain) | 1. Attempt an outbound from the ingest-pull path to the unlisted host. 2. Attempt the same from the model-download path. | The connection is **REFUSED** (default-deny), **not** merely logged and allowed; an explicit `EgressDenied`-class error is surfaced naming the refused host; **no socket** to the unlisted host is opened (100% unlisted-host refusal, NFR-013) |
| TC-114 | FR-024/AC-002, FR-027/AC-002 (NFR-013) | integration | P1 | **Adversarial (ADR-0023 §6e#2).** Allow-list = `{tnexwm.atlassian.net, huggingface.co}`; a connector is configured for a host **outside** the allow-list | 1. Positive: reach an allow-listed host (`*.atlassian.net`, then `huggingface.co` on the model path) → permitted. 2. Negative: a connector configured to an **unconfigured** host attempts to connect. | Allow-listed hosts (`*.atlassian.net`, `huggingface.co`) are **permitted**; the unconfigured host **fails closed** with a clear error and **no connection is attempted** (fail-closed before dial); allow-list honoured both ways |

## CHG-003 · FR-025 Least-privilege read-only Atlassian credential (incl. §6e#3 token-never-leaked, E-003)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-115 | FR-025/AC-002 (NFR-014) | unit | P1 | **Adversarial (ADR-0023 §6e#3, L-001/E-003).** A least-privilege fake token supplied via `MCP_CONFLUENCE_API_TOKEN(_FILE)`; a forced upstream error on the credential path that **interpolates the token into the error context** | 1. Trigger the forced error (bad-request / exception carrying the credential context). 2. Capture the rendered **tool result / ErrorEnvelope**. 3. Capture the **stderr log** line. 4. Grep both for the token value and for a redaction marker. | The token value is **absent** from the tool result **and** the stderr log — `scrub()` applied **both ways** (tool/result boundary and error/log path, ADR-0015); only a redaction marker (e.g. `***`) appears; **0** token leaks (NFR-014). Guards **E-003** (token-leak class): names E-003 as the class this regresses against |
| TC-116 | FR-025/AC-003 (NFR-014) | integration | P1 | **Negative.** A token whose Atlassian account **can write** (sampled `operations` include a write verb); reuses `ConfluenceClient.credential_check` | 1. Run `mcp-confluence doctor`. 2. Call `build_server()`. | `doctor` **refuses** the write-capable account, naming the permitted write operation(s); `build_server()` **does not serve** (startup read-only gate, ADR-0003 A1/ADR-0007 A2). The only bypass is `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`, which logs **WARN** each start and must not be used for the real token (100% write-capable refusal, NFR-014) |
| TC-117 | FR-025/AC-001 (NFR-014) | integration | P1 | A least-privilege **read-only** fake token via `MCP_CONFLUENCE_API_TOKEN_FILE` (and the env-var path); reachable `respx` Confluence Cloud fixture | 1. Run `mcp-confluence doctor` (both the `_FILE` and env-var supply paths). 2. Inspect doctor stdout + stderr. | doctor authenticates, proves the account is **read-only** (cannot write), and reports ok; the token value appears **nowhere** in stdout, logs, or the doctor output; `*_FILE` and env var both load correctly (never committed — `.env`/`*.env` git-ignored) |

## CHG-003 · FR-023 Real ingestion from Confluence Cloud via the CLI (Confluence first)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-118 | FR-023/AC-001 (NFR-013) | E2E | P1 | **CI / fixtures.** `respx` captured Confluence **Cloud** payloads (`/wiki/rest/api/...`); fake read-only token; egress guard (TC-111/TC-112) active; compose Postgres at 0008; MCP servers spawned over stdio | 1. `mcp-confluence doctor`. 2. `mcp-ingest run --source confluence`. 3. `mcp-ingest status --json`. 4. `kb_semantic_search` for ingested content over the real stdio tool round-trip. | doctor reports read-only ok; run exits `0` (success) with per-source counts; `status` shows Confluence with a recent `last_success_at` and document/chunk counts **> 0**; `kb_semantic_search` returns `status=ok` with a citation whose `source_uri` resolves to `tnexwm.atlassian.net`. End-to-end against **fixtures + fake token** — the real-tenant run is TC-121 (`@live`, not in `make ci`) |
| TC-119 | FR-023/AC-002 | integration | P1 | **Negative.** Confluence source made unreachable mid-run (`respx` network/credential failure); previously stored embeddings present | 1. Run `mcp-ingest run --source confluence` against the failing source. 2. Inspect checkpoint, prior embeddings, and exit status. | The failure is recorded for that source; the **checkpoint does not advance**; previously stored embeddings are **not** corrupted; exit `status=partial`/`failed` with per-source counts; **no fabricated** ingested content (carries FR-012/AC-002, R18) |
| TC-120 | FR-023/AC-003 (NFR-013) | integration | P1 | **Regression, EB-001/BR-014.** A real Confluence ingest run via `respx` recording **every** HTTP verb issued to the source | 1. Run `mcp-ingest run --source confluence`. 2. Assert the writes went only to `kb.*` under `mcp_ingest_rw`. 3. Assert the verbs issued to Confluence. | Writes go **only** to `kb.*` under role `mcp_ingest_rw`; **zero** write/update/delete reach Confluence; the transport issues **only GET/HEAD** to the source (ADR-0003 transport allow-list) — read-only-to-source holds (feeds the §6e#4 server-read-only assertion) |
| TC-121 | FR-023/AC-001 (NFR-013) | E2E | P2 | **`@live` — NOT in `make ci`.** The CEO's **real** read-only token for `tnexwm.atlassian.net` + VPN + online egress; `MCP_INGEST_ALLOW_LIVE_EGRESS=true` | 1. Operator runs `mcp-confluence doctor` → `mcp-ingest run --source confluence` → `status` → `kb_semantic_search` against the **real** tenant. | Same expectations as TC-118 but against the **live** `tnexwm.atlassian.net`; citation resolves to a real Confluence page. **Marked `@live` / live-egress: skipped in `make ci`, run by the operator (CEO) behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`; its skip-in-CI must be stated in the regression report (R1), never silently dropped** |
| TC-122 | FR-023/AC-004 | integration | P2 | Connector registry on disk = `{confluence, gitlab, opensearch, jira}`; `respx` fixtures for GitLab / OpenSearch / Jira | 1. Run `mcp-ingest run --source <gitlab\|opensearch\|jira>` following the same doctor→run→status→verify shape (OpenSearch only for an allow-listed index, default off — ADR-0012 A5). | The same pull/ingest/verify path applies per source with **no** connector-specific relaxation of the egress or read-only invariants; each produces per-source counts and a resolving citation; an unconfigured OpenSearch index stays off (default-deny) |

## CHG-003 · FR-026 CLI operability for all 9 sources — two classes (runbook acceptance)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-123 | FR-026/AC-001 | E2E | P1 | **Ingestable class.** One of the 4 ingestable sources configured read-only + reachable (`respx` fixtures); MCP servers over stdio | 1. `mcp-<src> doctor` → `mcp-ingest run --source <src>` → `mcp-ingest status` → `kb_semantic_search`. | doctor reports read-only ok; run completes with per-source counts; `status` shows the source; `kb_semantic_search` returns content from it with a **resolving citation**. The ingestable-class runbook shape (doctor→ingest→status→verify) holds end-to-end |
| TC-124 | FR-026/AC-001 (NFR-007) | integration | P1 | **Adversarial permission regression, E-007.** A **real-ingested** `restricted` Confluence document (via TC-118 path) whose content matches a crafted query; an un-granted caller (default-deny) | 1. Issue the crafted query via `search_company_knowledge` as the un-granted caller. 2. Inspect candidate set, context-pack, citations. | The restricted real-ingested doc is **not** a candidate, **not** in the pack, and **not** cited — excluded **before** context assembly at the unchanged `enforce_permission` choke point #1 (fails if any leak). Confirms CHG-003's real content inherits team-only default-deny; guards **E-007** (permission choke-point class) |
| TC-125 | FR-024/AC-003, FR-025/AC-003, FR-023/AC-003 (NFR-012/NFR-013/NFR-006) | E2E | P1 | **Adversarial / invariant (ADR-0023 §6e#4).** The 9 MCP servers + Jira MCP spawned over real stdio; process/socket inspection; a write-capable account fixture for the doctor arm | 1. Inspect each of the 9 servers + Jira for any **listening socket**. 2. Assert each makes **no** outbound call beyond its own upstream read API during a `tools/call`. 3. Re-assert `doctor` refuses a write-capable Atlassian account (cross-ref TC-116). | **0** listening sockets (no new network port — stdio kept, NFR-012); **0** server outbound beyond its own upstream read API (egress is confined to the `mcp-ingest` pull + the one-time model download, NFR-013); `doctor` refuses the write-capable account. **Regression over the relaxed-egress change** — the servers themselves did not gain egress or a port |
| TC-126 | FR-026/AC-004 (NFR-006/NFR-012) | E2E | P1 | **Regression, EB-001/BR-006.** Each of the 9 sources + Jira served over real stdio; `tools.snapshot.json` per server | 1. For each server, run `tools/list` over stdio and assert **0 write tools**. 2. Assert the surface count stays **61/62** (CHG-003 adds no tool). 3. Assert `doctor` reports read-only ok where applicable; logs on **stderr only**. | Every source + Jira proven **read-only-to-source** (zero write tools; `doctor` refuses a write-capable account where applicable), consistent with FR-014/NFR-001/NFR-006 on the **real** surface; stdio round-trip unchanged, stderr-only logs |
| TC-127 | FR-026/AC-003 | integration | P1 | **Negative.** A live-only source (CloudWatch); connector registry = `{confluence, gitlab, opensearch, jira}` only | 1. Attempt `mcp-ingest run --source cloudwatch`. 2. Repeat for `kibana`/`kafka`/`redis`/`sqs_sns`. | The ingest CLI **does not accept** a live-only source as a corpus connector (rejected: not in the registry); the runbook never instructs an ingest for the 5 live-only sources (R-D9) |
| TC-128 | FR-026/AC-002 (NFR-006) | integration | P1 | **Live-only class.** One of the 5 live-only sources configured read-only + reachable (`respx` fixture for `tools/list`); a `claude_desktop_config.json` registration fixture | 1. `mcp-<src> doctor`. 2. Register it in `claude_desktop_config.json` (fixture). 3. `tools/list`. 4. Assert **no** `mcp-ingest run` is issued for it. | doctor reports read-only ok; the server appears with its tools and **zero** write tools; **no** `mcp-ingest run` is issued ("integrate" = reachable + read-only + registered, not ingested). **`@live` note:** the real registration + reachability against the live system is an operator step behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`, **not in `make ci`**; CI runs the fixture `tools/list` + registry assertion |

## CHG-003 · FR-027 Real embedding-model download enabling NFR-003 measurement (L-002)

| TC | Covers AC | Level | Priority | Preconditions | Steps | Expected |
|----|-----------|-------|----------|---------------|-------|----------|
| TC-129 | FR-027/AC-001, FR-027/AC-002 (NFR-013) | integration | P2 | **CI / model stub.** `huggingface.co` allow-listed for the download step only; a local **model stub** stands in for `bge-m3`; a case where the Atlassian ingest egress is open but the model download is **deferred** | 1. Run the controlled model-download step (stub); assert `HF_HUB_OFFLINE` flips online for the step then back to offline. 2. Run `mcp-ingest run` / serving; assert **0** outbound calls during serving. 3. Deferred-model case: run ingestion with the model download deferred. 4. Attempt a model-path host other than `huggingface.co`. | The pinned model (ADR-0010 provisional `bge-m3`, 1024d) is fetched; `HF_HUB_OFFLINE` returns to offline; serving embeds with **0** outbound calls. Deferred case: ingestion still proceeds with the existing provider (two egresses **separable**, ADR-0023 §6d); a non-`huggingface.co` host on the model path is **refused** (default-deny, FR-024/AC-002) |
| TC-130 | FR-027/AC-003 (NFR-014/NFR-003/NFR-010) | integration | P2 | **CONDITIONAL / TBD (L-002/E-004).** The eval harness; CI run with the model stub / deterministic fake provider; the real golden-set + measured recall/τ require the `@live` real-model run (TC-131) + spike S2 | 1. Run the eval harness. 2. Record the **verdict distribution** (FACT/LOW_CONFIDENCE/UNKNOWN/CONFLICT counts). 3. Inspect the envelope `calibration_status`. | `# THRESHOLD TBD (NFR-003/NFR-010, L-002/E-004 — opening HF egress ENABLES measurement but does NOT prove semantic quality; recall and τ_fact/τ_low stay UNVERIFIED until a real company golden-set bake-off (spike S2) is measured, a later eval task, NOT an AC here)`. The harness runs and **reports a verdict distribution**; the envelope reports `calibration_status: uncalibrated`; the TC **does not assert any recall number or τ value** and **does not invent one**; it passes on the honesty contract (uncalibrated carried, no number) only |
| TC-131 | FR-027/AC-001 (NFR-003) | integration | P2 | **`@live` — NOT in `make ci`.** Real `huggingface.co` egress + online network; `MCP_INGEST_ALLOW_LIVE_EGRESS=true`; the real `bge-m3` (≈2 GB, 1024d) | 1. Operator runs the real one-time model download. 2. Re-run the eval harness on real-model embeddings and record the **measured** verdict distribution / recall. | The real model downloads once; serving returns to offline. Any measured retrieval number is **recorded as a measured value for exactly what it measures** — it does **not** by itself promote NFR-003 to VERIFIED without the golden-set bake-off (spike S2). **Marked `@live` / live-egress: skipped in `make ci`, run by the operator behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true`; its skip-in-CI must be stated in the regression report (R1)** |

## CHG-003 · AC → TC traceability summary (uncovered = none)

All **17 CHG-003 AC id** (`requirements.md` FR-023…FR-027) map to ≥1 TC above; every **Must** FR
(FR-023/FR-024/FR-025/FR-026) has ≥1 **E2E** TC. FR-027 is **Should**.

| FR (priority) | AC ids | TC ids | has E2E? |
|---|---|---|---|
| FR-023 (Must) | AC-001, AC-002, AC-003, AC-004 | TC-118 (E2E), TC-119, TC-120, TC-121 (E2E `@live`), TC-122, TC-125 (E2E) | **yes** (TC-118, TC-121, TC-125) |
| FR-024 (Must) | AC-001, AC-002, AC-003 | TC-111, TC-112, TC-113, TC-114, TC-125 (E2E) | **yes** (TC-125) |
| FR-025 (Must) | AC-001, AC-002, AC-003 | TC-115, TC-116, TC-117, TC-125 (E2E) | **yes** (TC-125) |
| FR-026 (Must) | AC-001, AC-002, AC-003, AC-004 | TC-123 (E2E), TC-124, TC-126 (E2E), TC-127, TC-128 | **yes** (TC-123, TC-126) |
| FR-027 (Should) | AC-001, AC-002, AC-003 | TC-129, TC-130 (`# THRESHOLD TBD`), TC-131 (`@live`) | n/a (Should) |
| NFR-013 (egress default-deny) | — | TC-112, TC-113, TC-114, TC-120, TC-125, TC-129 |  |
| NFR-014 (read-only credential + honest NFR-003) | — | TC-115, TC-116, TC-117, TC-130 |  |
| NFR-012 (stdio kept — carried) | — | TC-125, TC-126 |  |
| NFR-003 (semantic quality — UNVERIFIED/TBD) | — | TC-130 (`# THRESHOLD TBD`), TC-131 (`@live`) |  |

**The four ADR-0023 §6e adversarial / invariant TCs (highest priority, L-001 one-choke-point + adversarial input):**
§6e#1 egress default-deny → **TC-113** · §6e#2 allow-list honoured / unlisted-host-refused → **TC-114** (+ TC-111 positive) ·
§6e#3 token-never-leaked (forced-error, scrub both ways, E-003) → **TC-115** · §6e#4 server read-only + stdio unchanged, 0 ports → **TC-125** (+ TC-116/TC-126 doctor-refusal & read-only surface).

**Live-only TCs (NOT part of `make ci` — need CEO real token + VPN + online egress, gated by `MCP_INGEST_ALLOW_LIVE_EGRESS=true`):**
TC-121 (live Confluence ingest), TC-131 (real `huggingface.co` model download), and the live-register arm of TC-128.
Their skip-in-CI must appear in the regression report (R1), never silently dropped.

**Conditional / UNVERIFIED (defer numeric bound; no number invented, L-002/E-004):** TC-130 (`# THRESHOLD TBD` —
reports verdict distribution + `calibration_status: uncalibrated`; recall/τ TBD until the golden-set bake-off, spike S2).

`chg003_uncovered_ac: []` — 17/17 CHG-003 AC id (FR-023…FR-027) covered; 79/79 total FR AC id
(37 base + 25 CHG-001 run + 17 CHG-003) covered.
