# MCP Data Platform — Requirements

## Glossary / domain entities
- **MCP server**: A local process (stdio transport) exposing read-only tools/resources for one data source to Claude Desktop/Code.
- **Data source**: One of the 9 systems in scope — Confluence, GitLab, OpenSearch, Kibana, CloudWatch, Kafka, Redis, SQS/SNS, Postgres+pgvector.
- **Read-only**: No tool exposed by an MCP server may create, update, or delete data in the underlying source.
- **Ingest/embedding pipeline**: A separate batch/scheduled process that crawls source systems, generates vector embeddings, and persists them (with source metadata) into Postgres+pgvector.
- **Semantic/vector search**: A similarity query against stored embeddings in Postgres+pgvector, returning the closest-matching content chunks.
- **Citation / traceability**: A reference (link, id, or equivalent) in Claude's answer that lets a user verify the underlying data point in its original system.
- **Requester/Power user**: Person asking Claude questions via Desktop/Code.
- **Dev/Engineer**: Team member looking up code/docs.
- **On-call/SRE**: Person investigating an incident using observability sources.
- **Phase 1 / 2 / 3**: Delivery order of the Must-have scope (not a priority cut) — Phase 1 = Confluence+GitLab, Phase 2 = OpenSearch/Kibana/CloudWatch/Kafka/Redis, Phase 3 = SQS/SNS + Postgres+pgvector query + ingest/embedding pipeline.

## Business rules
- **BR-001**: All 9 MCP servers are read-only. No tool/resource exposed by any MCP server may perform a write, update, or delete operation against its underlying source, in any phase.
- **BR-002**: Any Claude answer that uses data returned by an MCP tool must include a citation traceable to the originating source (e.g., Confluence page link, GitLab MR/issue link, CloudWatch log group/metric name, original-source link behind an embedding). Claude must not present fabricated/unsourced content as fact when tool data is available.
- **BR-003**: v1 assumes uniform access — every team member has the same read access to all 9 sources; no per-role or per-user filtering is implemented in v1 (role-based access control is an open question, see Open questions / NFR-006).
- **BR-004**: The architecture (all MCP servers + pipeline) must not preclude a future move to multi-user/remote hosting (HTTP+SSE), even though that is out of scope for v1 implementation.
- **BR-005**: The ingest/embedding pipeline must preserve source metadata (source type, original id/URL, ingestion/update timestamp) for every embedded chunk, so that FR-011/FR-013 citations can resolve back to the original system.

## Functional requirements

### Phase 1 — MVP (Knowledge & Code)

#### FR-001 Confluence MCP server (read-only)
- Source: Scope — MoSCoW, Phase 1
- Priority: Must
- Description: WHEN Claude requests documentation/page content from Confluence via the MCP server THE SYSTEM SHALL execute a read-only query against Confluence and return matching pages/content with a traceable source link.
- Acceptance criteria:
  - AC-001 Given the Confluence MCP server is configured with valid read credentials and a page/content matching the query exists, When Claude issues a search/lookup query, Then the server returns the matching page(s) with title, content excerpt, and a Confluence URL usable as citation.
  - AC-002 (negative) Given no Confluence content matches the query, When Claude issues the query, Then the server returns an explicit empty result (not an error, not fabricated content).
  - AC-003 (negative) Given any client attempts a write/update/delete call against the Confluence MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no data in Confluence is modified.

#### FR-002 GitLab MCP server (read-only)
- Source: Scope — MoSCoW, Phase 1
- Priority: Must
- Description: WHEN Claude requests repo, code, merge request, issue, or pipeline information from GitLab THE SYSTEM SHALL execute a read-only query against GitLab and return matching results with a traceable source link.
- Acceptance criteria:
  - AC-001 Given a project/file/MR/issue matching the query exists, When Claude issues a search/lookup query, Then the server returns the matching result(s) with a GitLab URL usable as citation.
  - AC-002 (negative) Given the query references a nonexistent project, file, MR, or issue, When Claude issues the query, Then the server returns an explicit "not found" result rather than a guessed/fabricated answer.
  - AC-003 (negative) Given any client attempts a write operation (e.g., create issue, merge, push), When the call is made, Then the server exposes no such tool / rejects the call, and no data in GitLab is modified.

#### FR-003 Cross-source answer synthesis for Dev knowledge lookup (Journey 1)
- Source: User journeys #1
- Priority: Must
- Description: WHEN a Dev asks Claude a question that spans documentation and code THE SYSTEM SHALL have Claude call both the Confluence and GitLab MCP servers as relevant and synthesize a single answer citing each source actually used.
- Acceptance criteria:
  - AC-001 Given a question with relevant content available in both Confluence and GitLab, When Claude answers, Then the final answer includes at least one Confluence citation and one GitLab citation.
  - AC-002 (negative) Given one of the two sources (e.g., Confluence) has no relevant content for the question, When Claude answers, Then the answer explicitly states that no matching documentation was found for that source instead of inventing a citation or content.

### Phase 2 — Observability & Incident

#### FR-004 OpenSearch MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests index/log/search information THE SYSTEM SHALL execute a read-only query against OpenSearch and return matching results with index/document identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given a query with matching log entries/documents in a specified index and time range, When Claude issues the query, Then the server returns matching entries with index name and document id/timestamp as citation.
  - AC-002 (negative) Given a query with no matches or an invalid index/time range, When Claude issues the query, Then the server returns an explicit empty/error result rather than fabricated log entries.

#### FR-005 Kibana MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests dashboards or visualizations related to a service/topic THE SYSTEM SHALL execute a read-only query against Kibana and return matching dashboard/visualization metadata with a traceable link.
- Acceptance criteria:
  - AC-001 Given a dashboard/visualization matching the requested service/topic exists, When Claude issues the query, Then the server returns its metadata and a Kibana link usable as citation.
  - AC-002 (negative) Given no dashboard/visualization matches, When Claude issues the query, Then the server returns an explicit "not found" result.

#### FR-006 CloudWatch MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests logs, metrics, or alarms THE SYSTEM SHALL execute a read-only query against CloudWatch and return matching data with log group/metric/alarm identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given a valid log group/metric/alarm name and time window with matching data, When Claude issues the query, Then the server returns the data with the log group/metric/alarm name and time range as citation.
  - AC-002 (negative) Given an invalid/nonexistent log group, metric, or alarm name, When Claude issues the query, Then the server returns an explicit error/empty result rather than fabricated data.

#### FR-007 Kafka MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests topic, message, or metadata information THE SYSTEM SHALL execute a read-only query/peek against Kafka and return matching data with topic/partition/offset identifiers for traceability, without altering consumer offsets used by other consumers or producing new messages.
- Acceptance criteria:
  - AC-001 Given an existing topic, When Claude requests topic metadata (partitions, message count) or a message sample, Then the server returns accurate metadata/messages with topic name, partition, and offset as citation.
  - AC-002 (negative) Given a nonexistent topic name, When Claude issues the query, Then the server returns an explicit "not found" error.
  - AC-003 (negative) Given any client attempts to produce/publish a message via the Kafka MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no message is produced.

#### FR-008 Redis MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests key/value/cache structure information THE SYSTEM SHALL execute a read-only lookup against Redis and return the key's value/type with the key name for traceability.
- Acceptance criteria:
  - AC-001 Given a key that exists in Redis, When Claude requests its value, Then the server returns the value and data type together with the key name as citation.
  - AC-002 (negative) Given a key that does not exist, When Claude requests its value, Then the server returns an explicit nil/"not found" response rather than a fabricated value.
  - AC-003 (negative) Given any client attempts a write command (SET, DEL, EXPIRE, etc.) via the Redis MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no key is modified.

#### FR-009 Cross-source incident investigation synthesis (Journey 2)
- Source: User journeys #2
- Priority: Must
- Description: WHEN an on-call/SRE asks Claude about an incident within a time window THE SYSTEM SHALL have Claude query CloudWatch/OpenSearch/Kibana, and Kafka/Redis when relevant, then synthesize a single consolidated answer citing each source used.
- Acceptance criteria:
  - AC-001 Given a service name and an incident time window with relevant data present in at least CloudWatch/OpenSearch and Kibana, When Claude answers, Then the answer includes log/metric excerpts and a dashboard reference, each individually cited to its source.
  - AC-002 (negative) Given one of the queried sources returns no data for the specified time window, When Claude answers, Then the answer explicitly states that gap (e.g., "no CloudWatch alarms found in this window") rather than inferring or fabricating data for that source.

### Phase 3 — Messaging & Vector

#### FR-010 SQS/SNS MCP server (read-only)
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN Claude requests queue/topic/message metadata THE SYSTEM SHALL execute a read-only query against SQS/SNS and return matching metadata with queue/topic identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given an existing queue/topic, When Claude requests its attributes (e.g., message count, name/ARN), Then the server returns accurate metadata with the queue/topic name or ARN as citation.
  - AC-002 (negative) Given a nonexistent queue/topic name, When Claude issues the query, Then the server returns an explicit "not found" error.
  - AC-003 (negative) Given any client attempts to send/delete/publish a message via the SQS/SNS MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no message is sent, deleted, or published.

#### FR-011 Postgres+pgvector MCP query server (read-only semantic search)
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN Claude submits a semantic/synthesis query THE SYSTEM SHALL execute a read-only vector similarity search against Postgres+pgvector and return the top-matching content chunks, each with metadata that resolves back to its original source for citation.
- Acceptance criteria:
  - AC-001 Given embedded data relevant to the query exists in Postgres+pgvector, When Claude issues the semantic search, Then the server returns the top-N matching chunks, each including original source type and original-source reference (e.g., original Confluence/GitLab URL) usable as citation.
  - AC-002 (negative) Given no embedded data meets the similarity threshold, When Claude issues the semantic search, Then the server returns an explicit empty result rather than a low-relevance match presented as a confident answer.
  - AC-003 (negative) Given any client attempts an insert/update/delete against the pgvector store via this MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no stored embedding is modified.

#### FR-012 Ingest/embedding pipeline
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN the ingest/embedding pipeline runs (scheduled or triggered) THE SYSTEM SHALL crawl configured source systems (Confluence, GitLab, OpenSearch, and others as configured), generate embeddings for new/changed content, and persist them into Postgres+pgvector together with source metadata needed for traceability (source type, original id/URL, ingestion/update timestamp).
- Acceptance criteria:
  - AC-001 Given a successful pipeline run against a configured source with new or changed content, When the run completes, Then the new/changed content is embedded and stored with source metadata, and is retrievable via FR-011 with a citation resolving to the original item.
  - AC-002 (negative) Given one configured source is unreachable or a crawl step fails during a run, When the run executes, Then the pipeline records/reports the failure for that source, continues or retries without crashing the whole run, and does not corrupt previously stored embeddings for other sources.
  - AC-003 Given content that was previously ingested and has since changed at the source, When the pipeline re-ingests that content, Then the stored embedding/metadata for that item is updated/replaced rather than duplicated indefinitely.

#### FR-013 Semantic + messaging answer synthesis (Journey 3)
- Source: User journeys #3
- Priority: Must
- Description: WHEN a user asks Claude a synthesis-level question THE SYSTEM SHALL have Claude query the Postgres+pgvector MCP server for semantically related chunks, and the SQS/SNS MCP server for message/queue state when relevant, then synthesize an answer citing the original sources behind the matched embeddings and any queue/topic reference used.
- Acceptance criteria:
  - AC-001 Given relevant embedded chunks exist and, when relevant, related queue/topic state exists, When Claude answers, Then the answer cites the original source(s) behind each matched embedding and the queue/topic reference if SQS/SNS data was used.
  - AC-002 (negative) Given the pgvector query returns no relevant matches, When Claude answers, Then the answer explicitly states that no relevant indexed data was found rather than fabricating an answer.

### Cross-cutting (applies to all phases)

#### FR-014 Absolute read-only enforcement across all 9 sources
- Source: Goals / Non-goals
- Priority: Must
- Description: WHEN any MCP server (any of the 9 sources, any phase) is invoked by a client THE SYSTEM SHALL guarantee that no exposed tool/resource can create, update, or delete data at the underlying source.
- Acceptance criteria:
  - AC-001 Given the full set of tools exposed by any of the 9 MCP servers, When the tool list is inspected/tested, Then it contains zero tools capable of a write/update/delete side effect on the underlying source.
  - AC-002 (negative) Given a malicious or malformed client request attempting to invoke a non-existent "write" tool name, When the request is made, Then the server rejects it with an error and no source data changes.

#### FR-015 Answer traceability / no-hallucination guarantee
- Source: Goals — "Đảm bảo mọi câu trả lời của Claude dựa trên dữ liệu thật, có thể trích dẫn/trace được về nguồn gốc"
- Priority: Must
- Description: WHEN Claude produces an answer using data returned by one or more MCP tools THE SYSTEM SHALL ensure the answer includes a citation traceable to each source used, and WHEN no tool returns relevant data THE SYSTEM SHALL ensure Claude states that no data was found instead of fabricating an answer.
- Acceptance criteria:
  - AC-001 Given a tool call returns data used in the final answer, When Claude responds, Then the response includes a citation (link/id) resolvable to that source for every distinct source used.
  - AC-002 (negative) Given all relevant tool calls return empty/no-match results, When Claude responds, Then the response explicitly states that no matching data was found across the queried sources, and does not present invented facts as if sourced.

## Non-functional requirements
- **NFR-001 (Security — read-only integrity)**: For all 9 MCP servers across all phases, 100% of attempted mutating operations (write/update/delete) via exposed tools must fail/be rejected. Verification: automated test suite enumerating each server's exposed tools and asserting no mutating tool exists, run per server before phase sign-off.
- **NFR-002 (Reliability — network/VPN timeout handling)**: Each MCP server connecting to a remote/internal service (Confluence, GitLab, OpenSearch, Kibana, CloudWatch) must surface an explicit, distinguishable timeout/connection error within a bounded time (threshold TBD — to be confirmed with SA/Lead per Open question 4) instead of hanging indefinitely. Verification: manual/integration test simulating an unreachable endpoint per server.
- **NFR-003 (Traceability measurement)**: On a defined sample set of test questions per phase (set TBD — Open question 1), the proportion of answers that include a valid, resolvable source citation when tool data was used must meet a threshold to be defined by PO after Phase 1 launch. Verification: manual review against the sample set.
- **NFR-004 (Pipeline freshness & cost)**: The ingest/embedding pipeline's run cadence and the resulting maximum data staleness must stay within a bound to be defined by PO/SA at Phase 3 design time (Open question 5). Verification: pipeline run logs showing last-successful-run timestamp per source, reviewed against the agreed bound once set.
- **NFR-005 (Local integration)**: Each MCP server must run locally via stdio and register successfully with Claude Desktop/Code without requiring any separate UI/web service. Verification: manual check that each server appears and responds in the Claude Desktop/Code MCP tool list after configuration.

## Out of scope
- Building a dedicated UI/web dashboard (all access is via Claude + MCP tools).
- Any write/update/delete capability on any of the 9 data sources.
- Multi-user/remote hosting (HTTP+SSE) implementation in v1 — architecture only must not block it later.
- Role-based access control / per-user permission filtering in v1 (pending PO/SA confirmation — see Open questions).
- Adding data sources beyond the 9 listed (Could-have, future).
- Caching/performance optimization for high-volume sources such as OpenSearch/Kafka (Could-have, future).
- Defining the final numeric thresholds for success metrics (% correct-with-citation, baseline investigation time reduction) — owned by PO, tracked as open questions.

## Traceability
| Must-have item in product-requirement.md | FR id(s) |
| --- | --- |
| Phase 1 — Confluence MCP server | FR-001, FR-003 |
| Phase 1 — GitLab MCP server | FR-002, FR-003 |
| Phase 2 — OpenSearch MCP server | FR-004, FR-009 |
| Phase 2 — Kibana MCP server | FR-005, FR-009 |
| Phase 2 — CloudWatch MCP server | FR-006, FR-009 |
| Phase 2 — Kafka MCP server | FR-007, FR-009 |
| Phase 2 — Redis MCP server | FR-008, FR-009 |
| Phase 3 — SQS/SNS MCP server | FR-010, FR-013 |
| Phase 3 — Postgres+pgvector MCP query server | FR-011, FR-013 |
| Phase 3 — Ingest/embedding pipeline | FR-012 |
| Goal — read-only across all 9 sources (Non-goals) | FR-014 |
| Goal — answers traceable to real data, no hallucination | FR-015 |

## Open questions (carried from product-requirement.md, unresolved — affect NFR-002/003/004 thresholds and BR-003)
1. Exact threshold for "answer correct + correctly cited" success metric (sample set, timeframe) — Owner: PO.
2. Baseline manual investigation time and target reduction % / timeframe — Owner: PO + on-call/SRE.
3. Whether role-based access control is needed across the 9 sources, or uniform access holds (affects BR-003) — Owner: PO + SA.
4. Whether internal network/VPN connectivity risk blocks implementation/testing of any specific source (Confluence/GitLab/OpenSearch/Kibana/CloudWatch) — Owner: SA/Lead.
5. Acceptable cost/latency and data freshness bound for the ingest/embedding pipeline (Phase 3) — Owner: PO + SA.

---

HANDOFF
status: done
artifacts: [docs/squad/mcp-data-platform/requirements.md]
counts: FR=15 AC=36 NFR=5
blocking: none — open questions 1-5 above are non-blocking for SA/Lead to proceed with architecture/design per phase, but should be resolved before final thresholds are locked (owners: PO, SA/Lead as noted).
