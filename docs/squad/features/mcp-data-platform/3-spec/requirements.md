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

### CHG-001 Company Knowledge tier — new domain entities (Option C + B4 Grounding)
> Added by the CHG-001 Option C extension (approved at Gate 1, ADR-0017 *accepted*; B4 Grounding in-scope per CTO D-004, ADR-0018). These entities build **on top of** the live 9-source read-only base, keeping every invariant above.
- **Company Knowledge**: A new business capability (not previously an FR) that answers questions **about the company** — services, repositories, teams, documents, current work — from an indexed corpus plus live Jira, and only states what has an official source. Delivered by the **Knowledge MCP** server + **Live Jira MCP** server.
- **Knowledge MCP**: A read-only MCP server exposing 8 Company-Knowledge tools (`search_company_knowledge`, `get_service`, `get_repository`, `search_code`, `get_jira_context`, `find_related_knowledge`, `get_knowledge_summary`, `get_document_version`) over hybrid-RAG + the grounding gate.
- **Live Jira MCP**: A read-only MCP server for Jira (source #10), 5 tools (`jira_search_issues`, `jira_get_issue`, `jira_list_projects`, `jira_get_sprint`, `jira_list_board_sprints`) — thin REST, Cloud and Server/DC flavors.
- **Hybrid grounded search**: A single retrieval that combines keyword (Postgres `tsvector`), vector (`pgvector`) and metadata filtering, fuses the two rankings with Reciprocal Rank Fusion (RRF), re-ranks with a **local offline** cross-encoder (`bge-reranker-v2-m3`, `HF_HUB_OFFLINE=1`), compresses context without losing provenance, then assembles a context-pack. No paid API, no network egress (ADR-0020).
- **RRF (Reciprocal Rank Fusion)**: A deterministic rank-fusion method (k=60) that merges the keyword and vector result lists into one ranking without needing comparable scores.
- **Reranker (local offline)**: A cross-encoder model loaded from local disk that re-scores candidate chunks for relevance. If its weights are unavailable, the system falls back to **RRF-only** behind an explicit flag and reports `reranker=disabled` (never silently degraded quality — L-002).
- **Context-pack**: The final bundle of claims + per-claim provenance + grounding verdicts returned to Claude. Produced by the **context-pack assembler**, which is the single grounding gate (choke point #2).
- **Grounding gate / B4 Grounding**: The single server-side choke point (context-pack assembler) where every claim gets a verdict — `FACT`, `LOW_CONFIDENCE`, `UNKNOWN`, or `CONFLICT` — and per-claim provenance, before leaving the server (ADR-0018).
- **Claim**: An atomic proposition about the company that retrieval proposes to put in the context-pack. The unit the grounding gate assigns a verdict to.
- **Evidence / provenance**: The traceable backing for a claim — the 6 fields `source`, `source_version`, `owner`, `updated_time`, `confidence`, `evidence` (`document_id` + `chunk_id`, resolvable via `kb_get_document`). A claim missing any required field value cannot be `FACT` (ADR-0018 §1).
- **Confidence (evidence-strength)**: A deterministic score `retrieval × agreement × freshness` labelled explicitly as **evidence strength, NOT the probability the claim is true** (L-002). It never promotes a claim with no evidence to `FACT`.
- **Verdict**: One of `FACT` (evidence + confidence ≥ τ_fact), `LOW_CONFIDENCE` (evidence, τ_low ≤ confidence < τ_fact), `UNKNOWN` (no valid evidence), `CONFLICT` (≥2 sources disagree on the same claim).
- **Calibration status**: `calibrated` | `uncalibrated` — marks whether the FACT↔LOW_CONFIDENCE thresholds (τ) have been measured on a real golden-set. `uncalibrated` until eval closes them (NFR-003 UNVERIFIED, blocked by HF egress — L-002).
- **Permission choke point (#1)**: The single server-side `enforce_permission()` function that filters documents by `document_permissions`/`visibility` **default-deny**, running **before** context assembly and the grounding gate, for **all 8** Knowledge-tier content tools (not only the 2 grounded ones), so a document the caller has no grant on is never a candidate, evidence, or tool result (ADR-0016/0021). The team-only corpus (ADR-0016 A1) is defence-in-depth behind this point, not the sole barrier.
- **Gateway-boundary (in-process)**: `mcp_gateway` — an in-process boundary (no network port, keeps stdio/NFR-005) doing routing, auth-context, rate-limit and append-only audit for the Knowledge + Live servers (ADR-0021).
- **Source authority / live-vs-knowledge**: Config that ranks sources by **fact type** (e.g. runtime/config → GitLab, architecture → Confluence, current work status → Jira) to annotate conflicts and decide whether a snapshot (knowledge) or live (Jira) value is more authoritative — configurable, never hardcoded into the prompt (spec §42).
- **Freshness horizon**: Per-fact-type age bound used to compute the freshness factor of confidence; an over-horizon fact is downweighted but a fact is not made false by age alone (ADR-0018 D1).

### CHG-003 real ingestion + real egress — new domain entities (Option B, lean)
> Added by the CHG-003 extension (CEO approved Gate 1 2026-10-02, Option B; CTO sizing D-006; ADR-0023 *proposed*). CHG-003 removes the stub: it opens real egress for the ingest **pull** path to the actual sources (Confluence Cloud first) and for a one-time embedding-model download, and introduces Atlassian as a real vendor with a stored read-only credential. It does **not** relax read-only-to-source, stdio (NFR-005), or the grounding/permission choke points. These entities build **on top of** everything already built.
- **Egress allow-list**: The explicit, default-deny list of outbound hosts the system is permitted to reach: the **configured source hosts of the ingest pull path only** (`*.atlassian.net` for Confluence Cloud first; GitLab / OpenSearch / Jira hosts as configured), plus `huggingface.co` for the one-time embedding-model download. Any outbound host not on the list is refused (ADR-0023 §6a).
- **Default-deny egress**: The policy that outbound network is denied unless the destination host is on the egress allow-list, enforced at a single structural choke point on the ingest/model-download path with an adversarial test (L-001). The 9 MCP servers + Jira MCP make **no** outbound call beyond their own upstream read API and keep stdio; egress is a property of the `mcp-ingest` pull path and the model download, not of a server answering the client.
- **Ingest pull path**: The `mcp-ingest run --source <src>` code path that reads from an external source over the network and writes only the internal corpus (`kb.*`) under role `mcp_ingest_rw`. It never writes back to the source (read-only-to-source preserved).
- **Confluence Cloud target**: The confirmed real source — `https://tnexwm.atlassian.net`, flavor Cloud (matches the locked baseline `confluence_flavor=Cloud`). The Confluence-first reference pattern runs end-to-end: doctor → ingest → status → verify via `kb_semantic_search`.
- **Read-only Atlassian token**: A single least-privilege, **READ-ONLY** Atlassian Cloud API token for `tnexwm.atlassian.net` plus the account email, stored via env / `*_FILE` only (`MCP_CONFLUENCE_API_TOKEN` or `MCP_CONFLUENCE_API_TOKEN_FILE`). Never committed, never logged, scrubbed on both the tool/result boundary and the error/log path (ADR-0023 §6c, ADR-0005/0015, L-001/E-003).
- **doctor (read-only credential check)**: Each package's `doctor` subcommand — also its health/ready check — that authenticates the credential and proves it cannot write; `build_server()` refuses to serve a write-capable account. The escape hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` logs WARN each start and must not be used for the real token.
- **Source class — ingestable vs live-only (runbook)**: The 9 sources split into **4 ingestable** (Confluence, GitLab, OpenSearch, Jira — connector exists; pulled into `kb.*`, embedded, searchable) and **5 live-only** (CloudWatch, Kibana, Kafka, Redis, SQS/SNS — read-only Live MCP only; never ingested). "Integrate" for a live-only source means **reachable + read-only + registered**, not ingested (ADR-0023 §A.0; connector registry on disk = {confluence, gitlab, opensearch, jira}).
- **Real embedding-model download**: Opening `huggingface.co` for the controlled one-time model download; `HF_HUB_OFFLINE` flips to online **only** for that step, then back to offline for all serving. This **enables measurement** of NFR-003 for the first time but does not itself prove semantic quality (L-002) — see NFR-014.

## Existing behaviour
> Brownfield: the live 9-source read-only base (FR-001..FR-015) is already go-live. The CHG-001 Option C + B4 extension **must not break** the following rules, which existing contract/regression tests already enforce (see `6-verify/` and `4-design/api-contract.yaml`). New FRs add to these; they do not relax them.
- **EB-001 Read-only across all sources is absolute (FR-014, NFR-001, ADR-0003).** No MCP server exposes any write/update/delete tool; 100% of attempted mutating operations are rejected; the credential/role itself is verified read-only at startup and `build_server()` refuses to serve otherwise. **The 2 new servers (Knowledge MCP, Live Jira MCP) and all 13 new tools inherit this unchanged** — see FR regression ACs below. (`api-contract.yaml`: every new tool carries `x-readonly: true`, `x-side-effects: none`.)
- **EB-002 Envelope + citation contract (FR-015, BR-002, ADR-0004).** Every successful result with `status ∈ {ok, partial}` carries ≥1 citation; `empty`/`not_found` are *statuses*, not errors; a shared `Error` schema covers failures. The grounded envelope (`GroundedResultBase`) **extends** this backward-compatibly (adds `claims[]` + `grounding_summary`, adds `status=insufficient_evidence` only for grounded results) and must keep the base invariants for the 9 base sources.
- **EB-003 Semantic search exists but is simple (FR-011).** `mcp-pgvector` already runs plain vector similarity over `kb.chunks` returning an ADR-0004 envelope + citations. CHG-001 adds hybrid retrieval + reranking + grounding on top; the base `kb_semantic_search`/`kb_get_document`/`kb_list_sources` tools and their ACs must keep passing.
- **EB-004 Single Postgres, one embedding model (ADR-0010/0011).** `mcp-pgvector` refuses to serve if the configured embedding model/dimension differs from stored data; `deleted_at IS NULL` filters every query; `UNIQUE(source_type, source_id)` makes re-ingest an UPDATE. New domains (versions/entities/relationships/summaries/permissions) live in the **same** schema `kb` with no new datastore/extension.
- **EB-005 stdio-only (NFR-005).** Every server runs locally over stdio with no auxiliary network service; logs go to stderr only. The new gateway/orchestrator stays **in-process** and opens no network port.
- **EB-006 Egress/vendor boundary — relaxed only for the ingest pull + model download (CHG-003, ADR-0023).** Before CHG-003 the running system made **no** outbound call off the host (vendors=none, no-egress; real sources stubbed). CHG-003 opens egress **only** for (a) the `mcp-ingest` pull path to the configured source hosts (`*.atlassian.net` first) and (b) a one-time `huggingface.co` model download — nothing else. The following existing invariants **must keep holding** on the new surface and are carried as regression ACs below: **read-only-to-source** (ingest reads the source, writes only `kb.*` under `mcp_ingest_rw`; the 9 MCP servers + Jira never write to any source — EB-001/BR-001/BR-006); **stdio / no new network port** (NFR-005/EB-005 — no MCP server opens a port; egress belongs to the pull/download path, not to a server answering the client); **secret handling** (env/`*_FILE` only, never committed, `scrub()` both ways — EB-001/ADR-0005/0015, L-001/E-003); the grounding/permission choke points (BR-007..BR-009) are untouched. A host **not** on the egress allow-list must be refused (default-deny).
- Any FR below marked with an **EB-xxx regression AC** carries that existing rule forward as a testable criterion on the new surface.

## Business rules
- **BR-001**: All 9 MCP servers are read-only. No tool/resource exposed by any MCP server may perform a write, update, or delete operation against its underlying source, in any phase.
- **BR-002**: Any Claude answer that uses data returned by an MCP tool must include a citation traceable to the originating source (e.g., Confluence page link, GitLab MR/issue link, CloudWatch log group/metric name, original-source link behind an embedding). Claude must not present fabricated/unsourced content as fact when tool data is available.
- **BR-003**: v1 assumes uniform access — every team member has the same read access to all 9 sources; no per-role or per-user filtering is implemented in v1 (role-based access control is an open question, see Open questions / NFR-006).
- **BR-004**: The architecture (all MCP servers + pipeline) must not preclude a future move to multi-user/remote hosting (HTTP+SSE), even though that is out of scope for v1 implementation.
- **BR-005**: The ingest/embedding pipeline must preserve source metadata (source type, original id/URL, ingestion/update timestamp) for every embedded chunk, so that FR-011/FR-013 citations can resolve back to the original system.

### CHG-001 Option C + B4 Grounding (new rules, do not relax BR-001..BR-005)
- **BR-006**: The 2 new servers (Knowledge MCP, Live Jira MCP) and all 13 new tools are **read-only** — zero write/update/delete tools; Jira exposes no create/transition/comment. This is BR-001 extended to source #10 and the Knowledge tier (ADR-0019, ADR-0003).
- **BR-007 (no fabrication)**: For Company-Knowledge answers, a claim may carry verdict `FACT` **only** when it has valid evidence (the 6 fields of ADR-0018 §1, with resolvable `document_id`+`chunk_id`). A claim with no official source gets verdict `UNKNOWN` and the fixed message "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này." The server never invents, infers, or fills in a claim without evidence. `confidence` never promotes a no-evidence claim to `FACT` (ADR-0018 §1/§3, L-002).
- **BR-008 (one grounding gate)**: The grounding verdict is assigned at **exactly one** server-side choke point — the context-pack assembler — through which every claim must pass before leaving the server. No bypass path may push a claim to the context-pack without the gate (ADR-0018 §2, L-001). This is **distinct** from the permission choke point (#1) and does not duplicate it.
- **BR-009 (permission before assembly)**: `enforce_permission()` runs **before** context assembly and the grounding gate, default-deny, for **all 8** Knowledge-tier content tools (not only the 2 grounded ones). A document the caller has no grant on is never retrieved, ranked, used as evidence, or returned by any content tool — it is removed at choke point #1 before retrieval ranking (ADR-0016/0021, spec §24/§43). The team-only corpus (ADR-0016 A1) is defence-in-depth behind this, not the sole barrier.
- **BR-010 (conflict exposed, not merged)**: When ≥2 sources disagree on the same claim, the gate emits `CONFLICT` and exposes **all** positions, each with its own full evidence; it never silently merges or picks a winner. It only annotates authority per the source-authority config (by fact type); the final explanation is left to Claude (ADR-0018 §4, spec §41/§42).
- **BR-011 (deterministic, no egress)**: Grounding verdict and confidence are **deterministic** (`retrieval × agreement × freshness`); no LLM/API call, no network egress; reranker/embedding are local offline (`HF_HUB_OFFLINE=1`), vendors=none kept. The FACT↔LOW_CONFIDENCE threshold stays **TBD** until measured on a real golden-set (no invented number — L-002/E-004) and the envelope is marked `calibration_status: uncalibrated` until then (ADR-0017, ADR-0018 D1).
- **BR-012 (stdio kept)**: The gateway-boundary and orchestrator are **in-process**; no network port is opened. NFR-005 (stdio-only) holds unchanged; HTTP+SSE is deferred to v1.1, architecture only must not block it (ADR-0021, BR-004).

### CHG-003 real ingestion + real egress (new rules, do not relax BR-001..BR-012)
- **BR-013 (default-deny egress, allow-list only)**: Outbound network is **denied by default**. The only permitted destinations are the **configured source hosts of the ingest pull path** (`*.atlassian.net` for Confluence Cloud first; GitLab/OpenSearch/Jira hosts as configured) plus `huggingface.co` for the one-time embedding-model download. Any outbound host not on the allow-list is refused at a single structural choke point with an adversarial test (ADR-0023 §6a/§6e, L-001). The 9 MCP servers + Jira MCP make no outbound call beyond their own upstream read API and keep stdio — no server opens a new network port.
- **BR-014 (read-only-to-source preserved through real ingestion)**: Opening egress for the pull path does **not** relax read-only-to-source. `mcp-ingest` **reads** the external source and **writes only** the internal corpus `kb.*` under role `mcp_ingest_rw`; it never mutates the source. The 9 MCP servers + Jira stay read-only (BR-001/BR-006). This is EB-001 carried onto the real-egress surface (ADR-0023 §4/§6).
- **BR-015 (least-privilege read-only credential, never leaked)**: The Atlassian Cloud credential is a **single least-privilege, READ-ONLY** API token for `tnexwm.atlassian.net` plus the account email, stored via **env or `*_FILE` only** — never committed, never logged, never returned in a tool result or error. `scrub()` applies on **both** the tool/result boundary and the error/log path (ADR-0005/0015, L-001/E-003). `doctor` **refuses a write-capable account**: `build_server()` does not serve if the credential cannot prove it is read-only (ADR-0003 A1 / ADR-0007 A2 / ADR-0023 §6c). The escape hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` logs WARN each start and must not be used for the real token.
- **BR-016 (model egress separable, NFR-003 enabled not proven)**: The `huggingface.co` egress is separable from the Atlassian egress — the Atlassian ingest path can run with the model download deferred. `HF_HUB_OFFLINE` flips online **only** for the controlled download step, then back to offline for all serving; the running MCP servers stay offline. Downloading a real embedding model **enables** measurement of NFR-003 but does **not** by itself prove semantic quality; the FACT↔LOW_CONFIDENCE τ and any recall number stay **TBD** until measured on a real golden-set (L-002/E-004, NFR-010/NFR-014) — no invented number (ADR-0023 §6d/§7). The model pin (ADR-0010, provisional `bge-m3`, 1024d) is finalised only after the bake-off (spike S2) runs on a real model.

## Functional requirements

### Phase 1 — MVP (Knowledge & Code)

### FR-001 Confluence MCP server (read-only)
- Source: Scope — MoSCoW, Phase 1
- Priority: Must
- Description: WHEN Claude requests documentation/page content from Confluence via the MCP server THE SYSTEM SHALL execute a read-only query against Confluence and return matching pages/content with a traceable source link.
- Acceptance criteria:
  - AC-001 Given the Confluence MCP server is configured with valid read credentials and a page/content matching the query exists, When Claude issues a search/lookup query, Then the server returns the matching page(s) with title, content excerpt, and a Confluence URL usable as citation.
  - AC-002 (negative) Given no Confluence content matches the query, When Claude issues the query, Then the server returns an explicit empty result (not an error, not fabricated content).
  - AC-003 (negative) Given any client attempts a write/update/delete call against the Confluence MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no data in Confluence is modified.

### FR-002 GitLab MCP server (read-only)
- Source: Scope — MoSCoW, Phase 1
- Priority: Must
- Description: WHEN Claude requests repo, code, merge request, issue, or pipeline information from GitLab THE SYSTEM SHALL execute a read-only query against GitLab and return matching results with a traceable source link.
- Acceptance criteria:
  - AC-001 Given a project/file/MR/issue matching the query exists, When Claude issues a search/lookup query, Then the server returns the matching result(s) with a GitLab URL usable as citation.
  - AC-002 (negative) Given the query references a nonexistent project, file, MR, or issue, When Claude issues the query, Then the server returns an explicit "not found" result rather than a guessed/fabricated answer.
  - AC-003 (negative) Given any client attempts a write operation (e.g., create issue, merge, push), When the call is made, Then the server exposes no such tool / rejects the call, and no data in GitLab is modified.

### FR-003 Cross-source answer synthesis for Dev knowledge lookup (Journey 1)
- Source: User journeys #1
- Priority: Must
- Description: WHEN a Dev asks Claude a question that spans documentation and code THE SYSTEM SHALL have Claude call both the Confluence and GitLab MCP servers as relevant and synthesize a single answer citing each source actually used.
- Acceptance criteria:
  - AC-001 Given a question with relevant content available in both Confluence and GitLab, When Claude answers, Then the final answer includes at least one Confluence citation and one GitLab citation.
  - AC-002 (negative) Given one of the two sources (e.g., Confluence) has no relevant content for the question, When Claude answers, Then the answer explicitly states that no matching documentation was found for that source instead of inventing a citation or content.

### Phase 2 — Observability & Incident

### FR-004 OpenSearch MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests index/log/search information THE SYSTEM SHALL execute a read-only query against OpenSearch and return matching results with index/document identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given a query with matching log entries/documents in a specified index and time range, When Claude issues the query, Then the server returns matching entries with index name and document id/timestamp as citation.
  - AC-002 (negative) Given a query with no matches or an invalid index/time range, When Claude issues the query, Then the server returns an explicit empty/error result rather than fabricated log entries.

### FR-005 Kibana MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests dashboards or visualizations related to a service/topic THE SYSTEM SHALL execute a read-only query against Kibana and return matching dashboard/visualization metadata with a traceable link.
- Acceptance criteria:
  - AC-001 Given a dashboard/visualization matching the requested service/topic exists, When Claude issues the query, Then the server returns its metadata and a Kibana link usable as citation.
  - AC-002 (negative) Given no dashboard/visualization matches, When Claude issues the query, Then the server returns an explicit "not found" result.

### FR-006 CloudWatch MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests logs, metrics, or alarms THE SYSTEM SHALL execute a read-only query against CloudWatch and return matching data with log group/metric/alarm identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given a valid log group/metric/alarm name and time window with matching data, When Claude issues the query, Then the server returns the data with the log group/metric/alarm name and time range as citation.
  - AC-002 (negative) Given an invalid/nonexistent log group, metric, or alarm name, When Claude issues the query, Then the server returns an explicit error/empty result rather than fabricated data.

### FR-007 Kafka MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests topic, message, or metadata information THE SYSTEM SHALL execute a read-only query/peek against Kafka and return matching data with topic/partition/offset identifiers for traceability, without altering consumer offsets used by other consumers or producing new messages.
- Acceptance criteria:
  - AC-001 Given an existing topic, When Claude requests topic metadata (partitions, message count) or a message sample, Then the server returns accurate metadata/messages with topic name, partition, and offset as citation.
  - AC-002 (negative) Given a nonexistent topic name, When Claude issues the query, Then the server returns an explicit "not found" error.
  - AC-003 (negative) Given any client attempts to produce/publish a message via the Kafka MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no message is produced.

### FR-008 Redis MCP server (read-only)
- Source: Scope — MoSCoW, Phase 2
- Priority: Must
- Description: WHEN Claude requests key/value/cache structure information THE SYSTEM SHALL execute a read-only lookup against Redis and return the key's value/type with the key name for traceability.
- Acceptance criteria:
  - AC-001 Given a key that exists in Redis, When Claude requests its value, Then the server returns the value and data type together with the key name as citation.
  - AC-002 (negative) Given a key that does not exist, When Claude requests its value, Then the server returns an explicit nil/"not found" response rather than a fabricated value.
  - AC-003 (negative) Given any client attempts a write command (SET, DEL, EXPIRE, etc.) via the Redis MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no key is modified.

### FR-009 Cross-source incident investigation synthesis (Journey 2)
- Source: User journeys #2
- Priority: Must
- Description: WHEN an on-call/SRE asks Claude about an incident within a time window THE SYSTEM SHALL have Claude query CloudWatch/OpenSearch/Kibana, and Kafka/Redis when relevant, then synthesize a single consolidated answer citing each source used.
- Acceptance criteria:
  - AC-001 Given a service name and an incident time window with relevant data present in at least CloudWatch/OpenSearch and Kibana, When Claude answers, Then the answer includes log/metric excerpts and a dashboard reference, each individually cited to its source.
  - AC-002 (negative) Given one of the queried sources returns no data for the specified time window, When Claude answers, Then the answer explicitly states that gap (e.g., "no CloudWatch alarms found in this window") rather than inferring or fabricating data for that source.

### Phase 3 — Messaging & Vector

### FR-010 SQS/SNS MCP server (read-only)
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN Claude requests queue/topic/message metadata THE SYSTEM SHALL execute a read-only query against SQS/SNS and return matching metadata with queue/topic identifiers for traceability.
- Acceptance criteria:
  - AC-001 Given an existing queue/topic, When Claude requests its attributes (e.g., message count, name/ARN), Then the server returns accurate metadata with the queue/topic name or ARN as citation.
  - AC-002 (negative) Given a nonexistent queue/topic name, When Claude issues the query, Then the server returns an explicit "not found" error.
  - AC-003 (negative) Given any client attempts to send/delete/publish a message via the SQS/SNS MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no message is sent, deleted, or published.

### FR-011 Postgres+pgvector MCP query server (read-only semantic search)
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN Claude submits a semantic/synthesis query THE SYSTEM SHALL execute a read-only vector similarity search against Postgres+pgvector and return the top-matching content chunks, each with metadata that resolves back to its original source for citation.
- Acceptance criteria:
  - AC-001 Given embedded data relevant to the query exists in Postgres+pgvector, When Claude issues the semantic search, Then the server returns the top-N matching chunks, each including original source type and original-source reference (e.g., original Confluence/GitLab URL) usable as citation.
  - AC-002 (negative) Given no embedded data meets the similarity threshold, When Claude issues the semantic search, Then the server returns an explicit empty result rather than a low-relevance match presented as a confident answer.
  - AC-003 (negative) Given any client attempts an insert/update/delete against the pgvector store via this MCP server, When the call is made, Then the server exposes no such tool / rejects the call, and no stored embedding is modified.

### FR-012 Ingest/embedding pipeline
- Source: Scope — MoSCoW, Phase 3
- Priority: Must
- Description: WHEN the ingest/embedding pipeline runs (scheduled or triggered) THE SYSTEM SHALL crawl configured source systems (Confluence, GitLab, OpenSearch, and others as configured), generate embeddings for new/changed content, and persist them into Postgres+pgvector together with source metadata needed for traceability (source type, original id/URL, ingestion/update timestamp).
- Acceptance criteria:
  - AC-001 Given a successful pipeline run against a configured source with new or changed content, When the run completes, Then the new/changed content is embedded and stored with source metadata, and is retrievable via FR-011 with a citation resolving to the original item.
  - AC-002 (negative) Given one configured source is unreachable or a crawl step fails during a run, When the run executes, Then the pipeline records/reports the failure for that source, continues or retries without crashing the whole run, and does not corrupt previously stored embeddings for other sources.
  - AC-003 Given content that was previously ingested and has since changed at the source, When the pipeline re-ingests that content, Then the stored embedding/metadata for that item is updated/replaced rather than duplicated indefinitely.

### FR-013 Semantic + messaging answer synthesis (Journey 3)
- Source: User journeys #3
- Priority: Must
- Description: WHEN a user asks Claude a synthesis-level question THE SYSTEM SHALL have Claude query the Postgres+pgvector MCP server for semantically related chunks, and the SQS/SNS MCP server for message/queue state when relevant, then synthesize an answer citing the original sources behind the matched embeddings and any queue/topic reference used.
- Acceptance criteria:
  - AC-001 Given relevant embedded chunks exist and, when relevant, related queue/topic state exists, When Claude answers, Then the answer cites the original source(s) behind each matched embedding and the queue/topic reference if SQS/SNS data was used.
  - AC-002 (negative) Given the pgvector query returns no relevant matches, When Claude answers, Then the answer explicitly states that no relevant indexed data was found rather than fabricating an answer.

### Cross-cutting (applies to all phases)

### FR-014 Absolute read-only enforcement across all 9 sources
- Source: Goals / Non-goals
- Priority: Must
- Description: WHEN any MCP server (any of the 9 sources, any phase) is invoked by a client THE SYSTEM SHALL guarantee that no exposed tool/resource can create, update, or delete data at the underlying source.
- Acceptance criteria:
  - AC-001 Given the full set of tools exposed by any of the 9 MCP servers, When the tool list is inspected/tested, Then it contains zero tools capable of a write/update/delete side effect on the underlying source.
  - AC-002 (negative) Given a malicious or malformed client request attempting to invoke a non-existent "write" tool name, When the request is made, Then the server rejects it with an error and no source data changes.

### FR-015 Answer traceability / no-hallucination guarantee
- Source: Goals — "Đảm bảo mọi câu trả lời của Claude dựa trên dữ liệu thật, có thể trích dẫn/trace được về nguồn gốc"
- Priority: Must
- Description: WHEN Claude produces an answer using data returned by one or more MCP tools THE SYSTEM SHALL ensure the answer includes a citation traceable to each source used, and WHEN no tool returns relevant data THE SYSTEM SHALL ensure Claude states that no data was found instead of fabricating an answer.
- Acceptance criteria:
  - AC-001 Given a tool call returns data used in the final answer, When Claude responds, Then the response includes a citation (link/id) resolvable to that source for every distinct source used.
  - AC-002 (negative) Given all relevant tool calls return empty/no-match results, When Claude responds, Then the response explicitly states that no matching data was found across the queried sources, and does not present invented facts as if sourced.

### CHG-001 Option C + B4 Grounding — Company Knowledge tier (Phase 3 extension)
> New capability approved at Gate 1 (Option C, ADR-0017 *accepted*) with B4 Grounding in-scope (CTO D-004, ADR-0018). All FRs below are Phase 3, `x-change: CHG-001`, built on the live base. Each adds ≥1 happy-path and ≥1 negative AC; read-only + grounding invariants are carried as regression ACs (EB-xxx). Numeric thresholds that cannot yet be measured are marked **TBD** with a reason (L-002), never invented.

### FR-016 Hybrid grounded search (Company Knowledge)
- Source: product-requirement.md (CHG-001 Company Knowledge), architecture.md "Knowledge MCP — Hybrid-RAG", ADR-0020; tool `search_company_knowledge`
- Priority: Must
- Description: WHEN Claude submits a Company-Knowledge query THE SYSTEM SHALL run a single hybrid retrieval (keyword `tsvector` ∪ vector `pgvector` ∪ metadata filter), fuse the rankings with RRF (k=60), re-rank with the local-offline cross-encoder (`bge-reranker-v2-m3`), compress context without losing provenance, and return a context-pack of grounded claims with citations — all deterministic, offline, no egress.
- Acceptance criteria:
  - AC-001 Given indexed corpus content relevant to the query exists, When Claude calls `search_company_knowledge`, Then the response has `status=ok`, returns ranked claims each with ≥1 citation resolvable to the original source, and `grounding_summary` counts the verdicts.
  - AC-002 (negative) Given no candidate chunk is relevant, When Claude calls `search_company_knowledge`, Then the response returns `status=empty` or `status=insufficient_evidence` with no fabricated claims (not an error, not a low-relevance match presented as confident).
  - AC-003 (fallback transparency) Given the reranker weights are unavailable (e.g. offline model not loaded), When the query runs, Then the system falls back to RRF-only and reports `grounding_summary.reranker=disabled` explicitly, and does not present the degraded ranking as full-quality (L-002).
  - AC-004 (regression, EB-005/EB-004) Given the search runs, When the request is served, Then it uses stdio in-process with no network egress and reads via `mcp_query_ro` (no write), and `HF_HUB_OFFLINE=1` is in effect.

### FR-017 Jira as source #10 (Live Jira MCP, read-only)
- Source: product-requirement.md (CHG-001), architecture.md "Live Jira MCP", ADR-0019; tools `jira_search_issues`/`jira_get_issue`/`jira_list_projects`/`jira_get_sprint`/`jira_list_board_sprints`; ingest `source_type='jira'`
- Priority: Must
- Description: WHEN Claude requests Jira issue/project/sprint information THE SYSTEM SHALL execute a read-only REST query against Jira (Cloud `/rest/api/3` or Server/DC `/rest/api/2` flavor resolved internally) and return matching results with a Jira URL/key as citation; and WHEN the ingest pipeline runs for Jira THE SYSTEM SHALL support incremental (`updated >=` watermark) and full-reconcile (tombstone) modes as for other sources.
- Acceptance criteria:
  - AC-001 Given a Jira issue/project/sprint matching the request exists and the caller can read it, When Claude calls the corresponding `jira_*` tool, Then the server returns the matching result(s) with issue key / project key / sprint id and a Jira URL usable as citation.
  - AC-002 (negative) Given the request references a nonexistent issue key / board / sprint, When Claude calls the tool, Then the server returns an explicit `not_found` (or `empty` for a list with no matches), not a fabricated issue.
  - AC-003 (negative, regression EB-001/BR-006) Given any client attempts a Jira write (create issue, transition, add comment), When the call is made, Then the Live Jira MCP exposes no such tool / rejects it with `not_permitted`, and no data in Jira is modified.
  - AC-004 Given a Jira full-reconcile run completes with the safety-valve satisfied, When an issue has disappeared at the source, Then its ingested document is tombstoned (`deleted_at` set) and excluded from future `search_company_knowledge` results, without duplicating on re-ingest.

### FR-018 Live-vs-Knowledge decision (freshness, conflict, source authority)
- Source: architecture.md "Live-vs-Knowledge & freshness (spec §42)", ADR-0018 §4; tools `search_company_knowledge`, `get_jira_context`
- Priority: Must
- Description: WHEN the same claim is available from both an indexed snapshot (knowledge) and a live source (Jira) and the values differ THE SYSTEM SHALL emit `CONFLICT`, attach an `authority_note` derived from the configurable source-authority (by fact type) and the freshness of each side, and expose both values with their provenance — never silently choosing one.
- Acceptance criteria:
  - AC-001 Given a knowledge snapshot and a live Jira value agree for a claim, When `get_jira_context`/`search_company_knowledge` runs, Then the claim is returned as a single grounded claim (not a conflict) with provenance from both and a `data_freshness`/staleness indicator in `meta`.
  - AC-002 (negative / conflict) Given the snapshot and the live value differ for the same claim, When the tool runs, Then the claim's verdict is `CONFLICT`, both positions appear with full provenance (`source_version`, `updated_time`), and `authority_note` reflects the configured source-authority for that fact type — with no hardcoded authority in the prompt.
  - AC-003 Given a fact's `updated_time` is older than its configured `freshness_horizon`, When confidence is computed, Then the freshness factor is reduced (claim downweighted) but the claim is not made false solely by age, and the staleness is observable in `meta`.

### FR-019 Server-side permission enforcement before context assembly
- Source: architecture.md "enforce_permission() choke point #1", ADR-0016/0021, spec §24/§43, L-001; applies to all 8 Knowledge-tier content tools (`search_company_knowledge`, `get_jira_context`, `search_code`, `get_service`, `get_repository`, `find_related_knowledge`, `get_knowledge_summary`, `get_document_version`)
- Priority: Must
- Description: WHEN any Company-Knowledge content tool is invoked THE SYSTEM SHALL enforce `enforce_permission()` as a single default-deny choke point **before** context assembly and **before** the grounding gate, for **all 8** content tools of the Knowledge tier (`search_company_knowledge`, `get_jira_context`, `search_code`, `get_service`, `get_repository`, `find_related_knowledge`, `get_knowledge_summary`, `get_document_version`) — not only the 2 context-pack (grounded) tools — so that documents the caller is not permitted to see are removed before retrieval ranking and can never become evidence or a tool result. The team-only corpus (ADR-0016 A1) is **defence-in-depth behind** this choke point, **not** the sole barrier against a leak.
- Acceptance criteria:
  - AC-001 Given the corpus contains documents the caller is permitted to see, When the caller queries, Then only permitted documents are retrieved, ranked, and cited.
  - AC-002 (negative / adversarial, L-001) Given the corpus contains a `restricted` document whose content would match the query but the caller has no grant, When the caller issues the query, Then that document does **not** appear as a candidate, is **not** in the context-pack, and is **not** cited as evidence — i.e. it is excluded **before** context assembly (default-deny), confirmed by an adversarial test feeding exactly that query.
  - AC-003 (structural, L-001) Given the set of code paths that reach context assembly or return content, When they are inspected/tested, Then permission filtering is applied at exactly one choke point every path passes through (no bypass) for **every one of the 8 content tools** — not just the 2 grounded tools — and permission runs strictly **before** the grounding gate. The team-only corpus invariant (ADR-0016 A1) is not relied on as the barrier; the choke point is.

### FR-020 Document versioning, entities/relationships, knowledge summaries
- Source: architecture.md "Document versioning / entities / summaries", ADR-0022; tools `get_document_version`, `find_related_knowledge`, `get_knowledge_summary`, `get_service`, `get_repository`
- Priority: Must
- Description: WHEN Claude requests a document's version history, a related-knowledge graph, or a knowledge summary THE SYSTEM SHALL read the corresponding `kb` domain (`document_versions`, `entities`+`relationships`, `knowledge_summaries`) read-only — version history (minimal current/superseded), bounded recursive-CTE traversal (depth ≤ 3, cycle-detected), and summaries with provenance.
- Acceptance criteria:
  - AC-001 Given a document with version history exists, When Claude calls `get_document_version`, Then the server returns its versions (`current`/`superseded`) with `source_version`/`author`/`source_updated_at` and a citation; and given an entity with relationships, `find_related_knowledge` returns edges up to the requested depth (≤3) each with `rel_type`, `depth` and a citation.
  - AC-002 (negative) Given the requested `document_id`/entity does not exist (or is tombstoned), When the tool is called, Then the server returns `not_found` (or `empty` when an existing entity simply has no edges/summary), not a fabricated version/edge/summary.
  - AC-003 (bound) Given a relationship graph with cycles or high fan-out, When `find_related_knowledge` traverses it, Then traversal is bounded to depth ≤ 3 with cycle detection and a fan-out LIMIT (terminates; no runaway).

### FR-021 B4 Grounding verdict + per-claim provenance (no fabrication)
- Source: ADR-0018 (B4 Grounding contract, GT-1..GT-7), architecture.md "B4 Grounding gate", spec §39/§40/§41, L-001/L-002; tool `search_company_knowledge` (and any grounded result)
- Priority: Must
- Description: WHEN the context-pack assembler closes a Company-Knowledge result THE SYSTEM SHALL assign each claim exactly one verdict (`FACT`/`LOW_CONFIDENCE`/`UNKNOWN`/`CONFLICT`) with per-claim provenance (the 6 ADR-0018 §1 fields), server-side — never trusting Claude to self-filter — such that: a claim with no valid evidence is `UNKNOWN` with the fixed message and is never fabricated; `confidence` is labelled evidence-strength and never promotes a no-evidence claim to `FACT`; and conflicting sources yield `CONFLICT` exposing all positions.
- Acceptance criteria:
  - AC-001 (GT-1 adversarial — no source → UNKNOWN) Given the corpus has **no** evidence for the asked proposition, When `search_company_knowledge` is called, Then **no** claim is `FACT`, the result is `UNKNOWN`/`status=insufficient_evidence`, and the fixed message "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này." appears — the server does not invent an answer.
  - AC-002 (GT-2 — source present → FACT with evidence) Given the corpus **has** evidence for the proposition, When the tool is called, Then the claim is `FACT` carrying all 6 provenance fields and its `evidence` (`document_id`+`chunk_id`) **resolves** via `kb_get_document` to exactly that chunk.
  - AC-003 (GT-4 adversarial — conflict exposed) Given two sources assert different values for the same claim, When the tool is called, Then the verdict is `CONFLICT`, **both** positions appear with full provenance, nothing is silently merged or chosen, and `authority_note` reflects the source-authority config.
  - AC-004 (GT-3 — missing provenance never becomes FACT) Given a candidate claim lacks a required evidence field (e.g. `evidence`/`updated_time`), When the gate processes it, Then it is **never** `FACT` (it becomes `UNKNOWN`/`LOW_CONFIDENCE`) and is not passed through ungraded.
  - AC-005 (GT-6 — confidence is a labelled proxy, L-002) Given any claim, When it is returned, Then `confidence` always carries `confidence_basis` = "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)" and `calibration_status` is `uncalibrated` (threshold τ = TBD, blocked by HF egress — see NFR-010), and there is no path by which a high `confidence` turns a no-evidence claim into `FACT`.
  - AC-006 (GT-5/GT-7 structural — single gate, provenance preserved, L-001) Given the code paths producing a context-pack, When inspected/tested, Then every claim leaving the server has passed the single grounding gate (no bypass), and context-compression before the gate preserves each claim's provenance ("never compress away provenance").

### FR-022 Gateway-boundary in-process (routing, auth-context, rate-limit, audit; stdio kept)
- Source: architecture.md "mcp_gateway (ADR-0021)", ADR-0021; keeps NFR-005
- Priority: Must
- Description: WHEN a client invokes any Knowledge or Live tool THE SYSTEM SHALL route it through an **in-process** gateway-boundary that performs routing/discovery, attaches auth-context, applies token-bucket rate-limiting, and writes an append-only audit record to stderr — **without** opening any network port (stdio/NFR-005 kept), while leaving a transport-adapter seam for a future HTTP v1.1.
- Acceptance criteria:
  - AC-001 Given a valid tool call to a Knowledge/Live server, When it is invoked, Then the gateway routes it to the correct server, records an append-only audit entry (to stderr) with request identity, and returns the tool result.
  - AC-002 (negative / rate-limit) Given a caller exceeds the configured rate-limit, When further calls arrive, Then the gateway rejects the excess with a `rate_limited` error (with `retry_after_s`) rather than overloading the upstream, and the rejection is audited.
  - AC-003 (regression, EB-005/BR-012) Given the gateway is running, When the process is inspected, Then it has opened **no** network port (stdio only); all gateway logs/audit go to stderr, never stdout.

### CHG-003 — Real ingestion + real egress, CLI-driven, 9-source runbook (Confluence first)
> New capability approved at Gate 1 (Option B, lean track; CTO sizing D-006; ADR-0023 *proposed*). All FRs below are `x-change: CHG-003`, built on the live base + CHG-001. Each adds ≥1 happy-path and ≥1 negative AC; read-only-to-source, stdio and secret-handling invariants are carried as regression ACs (EB-001/EB-005/EB-006). Numeric thresholds that cannot yet be measured are marked **TBD-with-reason** (L-002), never invented. These FRs map to the ADR-0023 §6e adversarial/invariant tests (egress default-deny, unlisted-host-refused, token-never-leaked, server-read-only-unchanged).

### FR-023 Real ingestion from Confluence Cloud via the CLI (Confluence first)
- Source: plan-approval.md CHG-003 §"Phạm vi được duyệt" item 1 + §"Runbook CLI"; D-006 §3(a)/§3(c); ADR-0023 §1/§6a + Appendix A.1; the generalisable connector path (ADR-0019/0012); `mcp-ingest run --source` (`api-contract.yaml` `ingest_run`, enum `confluence|gitlab|opensearch|jira`)
- Priority: Must
- Description: WHEN an operator runs `mcp-ingest run --source confluence` against the real Confluence Cloud target `https://tnexwm.atlassian.net` THE SYSTEM SHALL pull documents over the (allow-listed) egress, redact → chunk → embed → upsert them idempotently into `kb.*` under role `mcp_ingest_rw`, and make them retrievable via `kb_semantic_search` with a citation resolving to the Confluence page — using the **same connector path** that generalises to GitLab/OpenSearch/Jira; Confluence is the end-to-end reference run first.
- Acceptance criteria:
  - AC-001 Given a valid read-only Confluence credential and reachable `https://tnexwm.atlassian.net`, When the operator runs `mcp-confluence doctor` then `mcp-ingest run --source confluence` then `mcp-ingest status --json`, Then doctor reports read-only ok, the run exits `0` (success) or `1` (partial) with per-source counts, status shows Confluence with a recent `last_success_at` and document/chunk counts > 0, and a subsequent `kb_semantic_search` for ingested content returns `status=ok` with a citation whose `source_uri` resolves to `tnexwm.atlassian.net`.
  - AC-002 (negative) Given the Confluence source is unreachable (e.g. network/credential failure) during a run, When `mcp-ingest run --source confluence` executes, Then the pipeline records the failure for that source, does **not** advance the checkpoint, continues/exits without corrupting previously stored embeddings, and reports `status=partial`/`failed` — it does not fabricate ingested content (carries FR-012 AC-002).
  - AC-003 (regression, EB-001/BR-014 — read-only-to-source) Given a real Confluence ingest run, When it writes, Then it writes **only** to `kb.*` under `mcp_ingest_rw` and performs **zero** write/update/delete against Confluence (verified: the ingest/transport path issues only GET/HEAD to the source — ADR-0003 transport allow-list; adversarial "server read-only unchanged" test, ADR-0023 §6e#4).
  - AC-004 (generalisation) Given the connector registry on disk holds `{confluence, gitlab, opensearch, jira}`, When the operator runs `mcp-ingest run --source <gitlab|opensearch|jira>` following the same doctor→run→status→verify shape, Then the same pull/ingest/verify path applies per source (OpenSearch only for allow-listed indices, default off — ADR-0012 A5), with no connector-specific relaxation of the egress or read-only invariants.

### FR-024 Default-deny egress with a source-host allow-list
- Source: plan-approval.md CHG-003 §"Phạm vi được duyệt" items 1–2 + §"Bất biến vẫn giữ"; D-006 §1/§3(a)/C2; ADR-0023 §6a/§6e#1–#2; `api-contract.yaml` `ingest_run` `x-egress`
- Priority: Must
- Description: WHEN any outbound network call is attempted on the ingest pull / model-download path THE SYSTEM SHALL permit it **only** if the destination host is on the egress allow-list — the configured source hosts of the ingest pull path (`*.atlassian.net` first; GitLab/OpenSearch/Jira hosts as configured) plus `huggingface.co` for the one-time model download — and SHALL refuse (default-deny) any host not on the list, enforced at a single structural choke point with an adversarial test; and THE SYSTEM SHALL ensure the 9 MCP servers + Jira MCP make no outbound call beyond their own upstream read API and open no network port (stdio kept).
- Acceptance criteria:
  - AC-001 Given the egress allow-list is configured with the source host(s), When the ingest pull path reaches a configured host (`tnexwm.atlassian.net`), Then the connection is permitted and ingestion proceeds.
  - AC-002 (negative / adversarial, L-001 — unlisted host refused) Given a host **not** on the allow-list (e.g. an arbitrary external domain), When the ingest/model-download path attempts to reach it, Then the connection is **refused** (not silently allowed), surfaced as an explicit error — proven by an adversarial test feeding exactly an unlisted host (ADR-0023 §6e#1).
  - AC-003 (regression, EB-005/EB-006/BR-013 — servers keep stdio) Given the 9 MCP servers + Jira MCP are running, When the processes are inspected, Then none has opened a network port and none makes an outbound call beyond its own upstream read API; egress is confined to the `mcp-ingest` pull path and the one-time model download (adversarial "server read-only/stdio unchanged" test, ADR-0023 §6e#4; NFR-005/NFR-012).

### FR-025 Least-privilege read-only Atlassian credential handling
- Source: plan-approval.md CHG-003 §"Phạm vi được duyệt" item 3 + §"Bất biến"; D-006 §3(b)/C3; ADR-0023 §6c/§6e#3; ADR-0005/0015, L-001/E-003
- Priority: Must
- Description: WHEN the system authenticates to Confluence Cloud (and the other Atlassian source, Jira) THE SYSTEM SHALL use a **single least-privilege READ-ONLY** API token for `tnexwm.atlassian.net` plus the account email, loaded via **env or `*_FILE` only**, never committed, never logged, never returned in any tool result or error (`scrub()` both ways); and `doctor` SHALL refuse a write-capable account at startup so `build_server()` does not serve it.
- Acceptance criteria:
  - AC-001 Given a least-privilege read-only token supplied via `MCP_CONFLUENCE_API_TOKEN_FILE` (or the env var), When `mcp-confluence doctor` runs, Then it authenticates, proves the account is read-only (cannot write), and reports ok — and the token value appears **nowhere** in stdout, logs, or the doctor output.
  - AC-002 (negative / adversarial — token never leaked, L-001/E-003) Given an error is forced on the credential path (e.g. an upstream failure that interpolates the credential context), When the error is rendered to a tool result and to the stderr log, Then the token value is **absent** from both — `scrub()` applies on the tool/result boundary **and** the error/log path (ADR-0015, ADR-0023 §6e#3).
  - AC-003 (negative — write-capable account refused) Given a token whose account can write to Confluence/Jira, When `doctor`/`build_server()` performs the startup read-only check, Then it **refuses to serve** (the account is rejected with the write operations named); the only bypass is `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`, which logs WARN each start and must not be used for the real token.

### FR-026 CLI operability for all 9 sources — two classes (runbook acceptance)
- Source: plan-approval.md CHG-003 §"Runbook CLI tích hợp 9 source"; D-006 §3(c)/§4/C4; ADR-0023 §A.0/§A.1/§A.2/§A.3; connector registry = {confluence, gitlab, opensearch, jira}; live-only = {cloudwatch, kibana, kafka, redis, sqs_sns}
- Priority: Must
- Description: WHEN an operator follows the integration runbook for the 9 sources THE SYSTEM SHALL make each source **reachable + read-only + registered** via its CLI, split honestly into two classes: the **4 ingestable** sources (Confluence, GitLab, OpenSearch, Jira) also run doctor → `mcp-ingest run` → `status` → verify via `kb_semantic_search`; the **5 live-only** sources (CloudWatch, Kibana, Kafka, Redis, SQS/SNS) run doctor → register in `claude_desktop_config.json` → `tools/list` smoke and are **never** ingested.
- Acceptance criteria:
  - AC-001 (ingestable class) Given one of the 4 ingestable sources is configured read-only and reachable, When the operator runs `mcp-<src> doctor` → `mcp-ingest run --source <src>` → `mcp-ingest status`, Then doctor reports read-only ok, the run completes with per-source counts, status shows the source, and `kb_semantic_search` returns content from it with a resolving citation.
  - AC-002 (live-only class) Given one of the 5 live-only sources is configured read-only and reachable, When the operator runs `mcp-<src> doctor`, registers it in `claude_desktop_config.json`, and lists tools, Then doctor reports read-only ok, the server appears with its tools (`tools/list`) and **zero** write tools, and **no** `mcp-ingest run` is issued for it ("integrate" = reachable + read-only + registered, not ingested).
  - AC-003 (negative — a live-only source is not ingested) Given a live-only source (e.g. CloudWatch), When the operator attempts `mcp-ingest run --source cloudwatch`, Then the ingest CLI does not accept it as a corpus connector (the connector registry holds only `{confluence, gitlab, opensearch, jira}`) — the runbook never instructs an ingest for the 5 live-only sources.
  - AC-004 (regression, EB-001/BR-006 — read-only across all 9 + Jira) Given each of the 9 sources' doctor/credential check runs, When inspected, Then every source + Jira is proven read-only-to-source (zero write tools; `doctor` refuses a write-capable account where applicable), consistent with FR-014/NFR-001/NFR-006 on the real surface.

### FR-027 Real embedding-model download enabling NFR-003 measurement
- Source: plan-approval.md CHG-003 §"Phạm vi được duyệt" item 2 (HuggingFace egress) + §"Baseline"; D-006 §3(d)/C5; ADR-0023 §6d/§7; ADR-0010 (model pin), DK1/D-002; L-002/E-004
- Priority: Should
- Description: WHEN the operator performs the controlled one-time embedding-model download THE SYSTEM SHALL allow egress to `huggingface.co` **only** for that step (flipping `HF_HUB_OFFLINE` online for the download, then back to offline for all serving), pin the model per ADR-0010 (provisional `bge-m3`, 1024d), and thereby **enable** real-model embedding so NFR-003 becomes measurable — while NOT claiming semantic quality is proven by enabling measurement (the recall number and τ stay TBD until a real golden-set bake-off runs; the measurement itself is a later eval task, not an AC here — L-002).
- Acceptance criteria:
  - AC-001 Given the HuggingFace egress is allow-listed for the download step only, When the operator runs the controlled model download, Then the pinned model is fetched, `HF_HUB_OFFLINE` returns to offline afterwards, and subsequent `mcp-ingest run` / serving embed with the real model while making **zero** outbound calls (serving stays offline).
  - AC-002 (negative — model egress is separable and bounded) Given the Atlassian ingest egress is open but the model download is deferred, When ingestion runs, Then it still proceeds (embedding with the existing provider) — the two egresses are separable (ADR-0023 §6d) — and no outbound call is made to `huggingface.co` outside the controlled download step; a host other than `huggingface.co` on the model path is refused (default-deny, FR-024 AC-002).
  - AC-003 (honesty, L-002 — enabling ≠ proving) Given a real embedding model has been downloaded and content embedded with it, When any retrieval metric is produced, Then NFR-003 semantic quality remains **UNVERIFIED** and the envelope keeps `calibration_status: uncalibrated` until a real-golden-set bake-off (spike S2) runs and records a measured number; no recall number or FACT↔LOW_CONFIDENCE τ is invented here (NFR-010/NFR-014). (The bake-off/eval is a later task, out of scope of this FR's ACs.)

## Non-functional requirements
- **NFR-001 (Security — read-only integrity)**: For all 9 MCP servers across all phases, 100% of attempted mutating operations (write/update/delete) via exposed tools must fail/be rejected. Verification: automated test suite enumerating each server's exposed tools and asserting no mutating tool exists, run per server before phase sign-off.
- **NFR-002 (Reliability — network/VPN timeout handling)**: Each MCP server connecting to a remote/internal service (Confluence, GitLab, OpenSearch, Kibana, CloudWatch) must surface an explicit, distinguishable timeout/connection error within a bounded time (threshold TBD — to be confirmed with SA/Lead per Open question 4) instead of hanging indefinitely. Verification: manual/integration test simulating an unreachable endpoint per server.
- **NFR-003 (Traceability measurement)**: On a defined sample set of test questions per phase (set TBD — Open question 1), the proportion of answers that include a valid, resolvable source citation when tool data was used must meet a threshold to be defined by PO after Phase 1 launch. Verification: manual review against the sample set.
- **NFR-004 (Pipeline freshness & cost)**: The ingest/embedding pipeline's run cadence and the resulting maximum data staleness must stay within a bound to be defined by PO/SA at Phase 3 design time (Open question 5). Verification: pipeline run logs showing last-successful-run timestamp per source, reviewed against the agreed bound once set.
- **NFR-005 (Local integration)**: Each MCP server must run locally via stdio and register successfully with Claude Desktop/Code without requiring any separate UI/web service. Verification: manual check that each server appears and responds in the Claude Desktop/Code MCP tool list after configuration.

### CHG-001 Option C + B4 Grounding (new NFRs)
- **NFR-006 (Read-only integrity — new surface)**: For the 2 new servers (Knowledge MCP, Live Jira MCP) and all 13 new tools, 100% of attempted mutating operations are rejected, and each server's credential/role is verified read-only at startup (`build_server()` refuses to serve otherwise). Verification: the same automated tool-surface + transport-allowlist suite as NFR-001, run against the new packages before sign-off; adversarial "write tool does not exist" test per server (L-001). Threshold: 100% (0 write tools, 0 successful mutations).
- **NFR-007 (Permission default-deny latency/correctness)**: Permission filtering (choke point #1) must exclude non-permitted documents in **100%** of adversarial cases before context assembly (no leak into candidates/context-pack/citations). Verification: adversarial test set per L-001; a measurable per-query permission-filter overhead budget is **TBD** (to be set by SA/Lead once the hybrid pipeline is measurable — carried with NFR-003/NFR-010 egress blocker), because end-to-end latency cannot be measured until the offline reranker runs.
- **NFR-008 (Grounding gate singularity — structural)**: Exactly **1** server-side choke point assigns the grounding verdict; **0** code paths reach the context-pack bypassing it. Verification: GT-5 structural "single choke point" test + GT-7 "compression preserves provenance" (ADR-0018 §6), run at the same tier as L-001 E-001..E-003.
- **NFR-009 (No-fabrication contract — adversarial)**: In the adversarial grounding suite GT-1..GT-4/GT-6, **0** no-evidence claims are returned as `FACT`, **100%** of no-source queries return `UNKNOWN`/`insufficient_evidence` with the fixed message, and **100%** of conflicting-claim cases return `CONFLICT` exposing all positions. Verification: GT-1..GT-7 contract tests (ADR-0018 §6) — these assert FACT/UNKNOWN/CONFLICT independent of the numeric threshold, so they run now.
- **NFR-010 (Confidence calibration — explicitly UNVERIFIED)**: The FACT↔LOW_CONFIDENCE thresholds (τ_fact, τ_low) are **TBD**, not invented (L-002/E-004): they are meaningful only measured on a real company golden-set, which is blocked by HuggingFace egress (NFR-003 remains UNVERIFIED, D-002/DK1). Until measured, the envelope reports `calibration_status: uncalibrated`; starter values (`τ_fact=0.6`, `τ_low=0.3`) are for infrastructure tests only and are **not** claimed as correct. Verification: eval run on the golden-set once egress is cleared, after which ADR-0018 moves to *accepted* with measured τ recorded.
- **NFR-011 (Deterministic / no egress)**: Grounding verdict + confidence make **0** network/LLM/API calls; reranker/embedding load offline (`HF_HUB_OFFLINE=1`); vendors=none holds. Verification: unit/integration test asserting no outbound socket during grounding; config assertion `HF_HUB_OFFLINE=1`; dependency/vendor audit.
- **NFR-012 (stdio kept — no new network port)**: The in-process gateway-boundary and orchestrator open **0** network ports; all logs/audit go to stderr, never stdout. Verification: process/port inspection in test (assert no listening socket) + stdout-write guard test (as NFR-005).

### CHG-003 real ingestion + real egress (new NFRs)
- **NFR-013 (egress default-deny guarantee)**: On the ingest-pull / model-download path, **100%** of outbound attempts to a host **not** on the egress allow-list are refused (default-deny); the 9 MCP servers + Jira MCP make **0** outbound calls beyond their own upstream read API and open **0** network ports. Threshold: 100% unlisted-host refusal, 0 server ports, 0 server egress beyond upstream read API. Verification: the ADR-0023 §6e adversarial tests at a single structural choke point (L-001) — #1 egress default-deny proven (reach an unlisted host → refused), #2 allow-list honoured (configured host reached, unconfigured denied); process/socket inspection asserting no MCP server opens a port (as NFR-005/NFR-012).
- **NFR-014 (read-only credential guarantee + honest NFR-003 enablement)**: The Atlassian credential is least-privilege read-only and never leaks: in **100%** of adversarial cases (including a forced error on the credential path) the token value appears in **0** logs, stdout, or tool results (`scrub()` both ways), and a write-capable account is refused at startup in 100% of cases (`build_server()` does not serve). Threshold: 0 token leaks, 100% write-capable-account refusal. Separately, opening `huggingface.co` **enables** NFR-003 measurement but does not prove it: the FACT↔LOW_CONFIDENCE τ (NFR-010) and any recall number stay **TBD** — *not applicable as a pass/fail threshold here because* the real semantic-quality number is only meaningful once measured on a real company golden-set, which is a later eval task (spike S2), not provable by enabling egress (L-002/E-004). Until then the envelope reports `calibration_status: uncalibrated` and no number is invented. Verification: ADR-0023 §6e#3 (token-never-leaked adversarial test on tool + error/log paths) and #4 (`doctor` refuses write-capable account); the NFR-003 recall/τ measurement is tracked as an open eval item, not asserted here.

## Data & privacy
> New with CHG-001 (the base feature's privacy posture is unchanged; this section makes the new tier explicit).
- **Personal/sensitive data touched**: Jira (source #10) issues may contain personal names, assignees, reporter emails, and free-text that can include sensitive business or personal information. The indexed corpus (`kb.chunks`) may contain content copied out of Confluence/GitLab/Jira. GitLab code/content may contain secrets.
- **Controls carried from the base**: redaction + deny-glob apply at **both** the tool layer and the ingest stage (`redact`, before chunk) so secrets/`.env`/tokens are never persisted into `kb.chunks` (ADR-0015 A1); `wrap_untrusted()` is applied only at the result boundary, never persisted. Jira ingest is subject to the same redaction + `visibility` default-deny.
- **Who may see it (access)**: The `kb` corpus is **team-only, default-deny** (ADR-0016) — the only place data is copied out of its source and read with a credential not tied to the asker. `enforce_permission()` (choke point #1, FR-019) filters by `document_permissions`/`visibility` before context assembly for **all 8** Knowledge-tier content tools (not only the 2 grounded ones); a document the caller has no grant on is never a candidate, evidence, or tool result. The team-only corpus is **defence-in-depth behind** this choke point, **not** the sole barrier. Live Jira results reflect the caller's own Jira read permissions (inherited from the source).
- **Retention**: `kb` embeddings/metadata follow the pipeline's existing tombstone + `prune` retention (NFR-004); knowledge summaries/versions are derived data under the same retention. No new long-term store is introduced (same single Postgres).
- **Audit**: The in-process gateway writes an append-only audit record (to stderr) per tool call (FR-022). No audit/log line may contain secrets (redaction applies; queries logged at DEBUG only, redacted).
- **PII in examples**: all examples use placeholders (e.g. `payment-service`, `PAY-1234`); no real PII is embedded in this spec.
- **(CHG-003) Real egress + stored credential — new privacy surface.** CHG-003 makes ingestion real: the system now reaches `tnexwm.atlassian.net` over the public internet and stores an Atlassian credential. **Credential**: a single least-privilege **read-only** API token + account email, env/`*_FILE` only, never committed (`.env`/`*.env` git-ignored), never logged or returned, scrubbed both ways; `doctor` refuses a write-capable account (BR-015/FR-025, NFR-014). **Egress**: default-deny, allow-list = configured source hosts + `huggingface.co` for the one-time model download only; a host not on the list is refused (BR-013/FR-024, NFR-013). **Data leaving the boundary**: only the ingest *pull* reads from the source; nothing is sent out — ingest writes only `kb.*` and never writes back to the source (BR-014). Real Confluence content now lands in `kb.chunks`, subject to the **same** redaction + deny-glob (ADR-0015 A1), team-only default-deny (ADR-0016) and `enforce_permission` choke point (FR-019) as the base corpus. Retention/tombstone/`prune` unchanged (NFR-004). The CEO accepted Atlassian as a real vendor at Gate 1 (D-006, Option B); no new paid tier is assumed (run-cost delta ≈ $0) — if a paid tier turns out to be required, Rule 3 fires again → back to the CEO.

## Out of scope
- Building a dedicated UI/web dashboard (all access is via Claude + MCP tools).
- Any write/update/delete capability on any of the 9 data sources.
- Multi-user/remote hosting (HTTP+SSE) implementation in v1 — architecture only must not block it later.
- Role-based access control / per-user permission filtering in v1 (pending PO/SA confirmation — see Open questions).
- Adding data sources beyond the 9 listed (Could-have, future).
- Caching/performance optimization for high-volume sources such as OpenSearch/Kafka (Could-have, future).
- Defining the final numeric thresholds for success metrics (% correct-with-citation, baseline investigation time reduction) — owned by PO, tracked as open questions.
- **(CHG-001)** HTTP+SSE / multi-user remote hosting of the gateway — deferred to v1.1; architecture only must not block it (BR-012/BR-004).
- **(CHG-001)** Full "company fact" lifecycle — governance status (Official/Active) and version-currency / full obsolete-propagation chain — deferred to B7 (Provenance) + B8 (Governance) backlog; the minimal versioning (current/superseded) ships now (FR-020).
- **(CHG-001)** Calibrated FACT↔LOW_CONFIDENCE thresholds (τ) — deferred until a real golden-set can be measured (blocked by HF egress; NFR-010); the no-evidence⇒UNKNOWN / CONFLICT invariants ship now.
- **(CHG-001)** Online/paid reranker or LLM-judge grounding, any external embedding/rerank API — explicitly excluded (vendors=none / no egress, BR-011).
- **(CHG-001)** A separate graph database for relationships — out; bounded recursive CTE in the same Postgres is used instead (ADR-0022).
- **(CHG-001)** Any Jira write (create/transition/comment) — out; Jira is read-only source #10 (BR-006).
- **(CHG-003)** The actual NFR-003 semantic-quality measurement / embedding bake-off (spike S2) and the calibrated FACT↔LOW_CONFIDENCE τ — out of this change's ACs; CHG-003 only **enables** measurement by opening the model download. The eval/bake-off is a later task (L-002/NFR-014).
- **(CHG-003)** Egress to any host beyond the configured source hosts + `huggingface.co` — out; default-deny, allow-list only (BR-013/FR-024). Any new vendor/paid tier/egress target → ESCALATE to the CEO (Rule 3), not in this change.
- **(CHG-003)** Ingesting the 5 live-only sources (CloudWatch, Kibana, Kafka, Redis, SQS/SNS) into the corpus — out; they are reachable + read-only + registered only (connector registry = {confluence, gitlab, opensearch, jira}; FR-026 AC-002/AC-003).
- **(CHG-003)** Any write-back to a source, new network port, or non-stdio transport for the MCP servers — out; read-only-to-source + stdio kept (BR-014/NFR-012/NFR-013).
- **(CHG-003)** Opening egress with the real token before the CEO's Gate-1 approval is recorded in plan-approval.md — out; the runbook is written but must not be run against real credentials until approved (ADR-0023 Appendix A header).

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
| **CHG-001** — Company Knowledge hybrid grounded search | FR-016 |
| **CHG-001** — Jira source #10 (read-only + ingest) | FR-017 |
| **CHG-001** — Live-vs-Knowledge / freshness / conflict / source authority | FR-018 |
| **CHG-001** — Permission server-side before context assembly | FR-019 |
| **CHG-001** — Document versioning / entities+relationships / summaries | FR-020 |
| **CHG-001 / B4** — Grounding verdict + per-claim provenance (no fabrication) | FR-021 |
| **CHG-001** — Gateway-boundary in-process (stdio kept) | FR-022 |
| **CHG-003** — Real ingestion from Confluence Cloud via CLI (Confluence first, generalises to GitLab/OpenSearch/Jira) | FR-023 |
| **CHG-003** — Default-deny egress + source-host allow-list (+ huggingface.co for model) | FR-024 |
| **CHG-003** — Least-privilege read-only Atlassian credential, never leaked; doctor refuses write-capable account | FR-025 |
| **CHG-003** — CLI operability for all 9 sources (4 ingestable + 5 live-only runbook) | FR-026 |
| **CHG-003** — Real embedding-model download enabling NFR-003 measurement | FR-027 |

### CHG-001 FR → tool / ADR traceability (for Lead + QA-plan)
| FR | New tool(s) / interface | ADR | Grounding tests |
| --- | --- | --- | --- |
| FR-016 | `search_company_knowledge` (hybrid+RRF+rerank+grounding envelope) | ADR-0020, ADR-0018, ADR-0004 | — (envelope shape); GT-* via FR-021 |
| FR-017 | `jira_search_issues`, `jira_get_issue`, `jira_list_projects`, `jira_get_sprint`, `jira_list_board_sprints`; ingest `source_type='jira'` | ADR-0019 (+0007/0012) | — |
| FR-018 | `search_company_knowledge`, `get_jira_context` (conflict/`authority_note`/`data_freshness`) | ADR-0018 §4, spec §42 | GT-4 (via FR-021 AC-003) |
| FR-019 | `enforce_permission()` (choke point #1) — all 8 Knowledge-tier content tools | ADR-0016, ADR-0021 | L-001 adversarial (FR-019 AC-002/AC-003) |
| FR-020 | `get_document_version`, `find_related_knowledge`, `get_knowledge_summary`, `get_service`, `get_repository` | ADR-0022 | — |
| FR-021 | `search_company_knowledge` / any `GroundedResult` — context-pack assembler (choke point #2) | ADR-0018 (§1–§6, D1) | GT-1..GT-7 (map to FR-021 AC-001..AC-006) |
| FR-022 | `mcp_gateway` (routing/auth-context/rate-limit/audit) — fronts all 13 new tools | ADR-0021 (+0002/0003) | — |

### GT (ADR-0018 §6) → FR-021 AC mapping (for QA-plan)
| ADR-0018 test | Intent | FR-021 AC |
| --- | --- | --- |
| GT-1 | no-source → UNKNOWN, fixed message, no FACT | AC-001 |
| GT-2 | source present → FACT, 6 fields, evidence resolves | AC-002 |
| GT-3 | missing provenance → never FACT | AC-004 |
| GT-4 | conflict → CONFLICT, all positions, authority_note | AC-003 (+ FR-018 AC-002) |
| GT-5 | single choke point, no bypass | AC-006 (+ NFR-008) |
| GT-6 | confidence is labelled proxy, cannot "rescue" | AC-005 (+ NFR-010) |
| GT-7 | compression preserves provenance | AC-006 (+ NFR-008) |

### CHG-003 plan-approval scope → FR (full coverage, uncovered = none)
| plan-approval.md CHG-003 approved scope item | FR id(s) |
| --- | --- |
| 1. Open egress to `*.atlassian.net` for the ingest pull to Confluence Cloud `tnexwm.atlassian.net`; Atlassian as real vendor | FR-023, FR-024 |
| 2. Open egress to `huggingface.co` to download a real embedding model (unblock NFR-003 measurement) | FR-027 (+ FR-024 allow-list) |
| 3. Read-only least-privilege API token via env/`*_FILE`, never committed/logged, scrub both ways; `doctor` refuses a write-capable account | FR-025 |
| 4. CLI runbook integrating all 9 sources, Confluence first — 4 ingestable (doctor→ingest→status→verify) + 5 live-only (doctor→register→tools/list) | FR-026 (+ FR-023 Confluence reference) |
| Kept invariants: 9 servers read-only + stdio (no new port); egress only for pull + model; permission/grounding choke points unchanged; token read-only; ingest writes only `kb.*` under `mcp_ingest_rw` | FR-024 AC-003, FR-023 AC-003, FR-025, FR-026 AC-004 (regression ACs) + EB-006 / NFR-012/NFR-013 |

### CHG-003 FR/AC → ADR-0023 §6e adversarial/invariant tests (for Lead + QA-plan)
| ADR-0023 §6e invariant test | Intent | FR/AC + NFR |
| --- | --- | --- |
| #1 Egress default-deny proven | an unlisted host is **refused**, not silently allowed | FR-024 AC-002; NFR-013 |
| #2 Allow-list honoured | a configured source host is reached; an unconfigured host is denied | FR-024 AC-001/AC-002; FR-027 AC-002; NFR-013 |
| #3 Token never leaks | token absent from logs/stdout/results; forced-error on credential path still scrubbed | FR-025 AC-002 (+ AC-001); NFR-014 |
| #4 Server read-only unchanged | 9 servers + Jira stay read-only-to-source + stdio, no new port; `doctor` refuses write-capable account | FR-023 AC-003; FR-024 AC-003; FR-025 AC-003; FR-026 AC-004; NFR-012/NFR-013/NFR-006 |

## Open questions (carried from product-requirement.md, unresolved — affect NFR-002/003/004 thresholds and BR-003)
1. Exact threshold for "answer correct + correctly cited" success metric (sample set, timeframe) — Owner: PO.
2. Baseline manual investigation time and target reduction % / timeframe — Owner: PO + on-call/SRE.
3. Whether role-based access control is needed across the 9 sources, or uniform access holds (affects BR-003) — Owner: PO + SA.
4. Whether internal network/VPN connectivity risk blocks implementation/testing of any specific source (Confluence/GitLab/OpenSearch/Kibana/CloudWatch) — Owner: SA/Lead.
5. Acceptable cost/latency and data freshness bound for the ingest/embedding pipeline (Phase 3) — Owner: PO + SA.
6. **(CHG-001)** FACT↔LOW_CONFIDENCE thresholds (τ_fact, τ_low) — **TBD**, cannot be set until a real company golden-set is measured (blocked by HuggingFace egress; NFR-003/NFR-010 UNVERIFIED). The no-evidence⇒UNKNOWN / CONFLICT invariants are enforced now independent of τ. Owner: SA/Lead + eval, after egress cleared. Non-blocking for build.
7. **(CHG-001)** Per-query permission-filter + hybrid-pipeline latency budget (NFR-007) — **TBD**, cannot be measured until the offline reranker runs (same egress blocker). Owner: SA/Lead. Non-blocking for build.
8. **(CHG-001)** Source-authority config by fact type (FR-018) — initial mapping proposed in architecture (runtime/config→GitLab, architecture→Confluence, current work→Jira, code behavior→GitLab); exact fact-type taxonomy + freshness_horizon values to confirm. Owner: SA/PO. Non-blocking (configurable, seeded in `0008_source_authority.sql`).
9. **(CHG-003)** Real NFR-003 recall number + calibrated τ once a real embedding model is downloaded (FR-027) — **TBD-with-reason**: still requires a real company golden-set bake-off (spike S2); opening egress only *enables* the measurement, it does not produce the number. Owner: SA/Lead + eval, after the model download. Non-blocking for the CHG-003 build (no number invented; `calibration_status: uncalibrated` carried).
10. **(CHG-003)** Which GitLab / OpenSearch / Jira hosts join the egress allow-list, and the per-source read-only scopes — to confirm as each ingestable source is turned on (Confluence `tnexwm.atlassian.net` is confirmed; others added only as configured, default-deny). Owner: SA/Lead + operator. Non-blocking (allow-list is configurable; default-deny holds for any unconfigured host).

> Note on BR-003: CHG-001 Gate-1 approval **inverts** the prior no-RBAC assumption **for the `kb` corpus only** — it is team-only, default-deny (ADR-0016, FR-019/BR-009). The 9 base live sources still inherit source permissions (BR-003 holds there); open question 3 remains for those.
> Note on CHG-003 vendors/egress: Gate-1 Option B (D-006) opened egress for the ingest pull (`*.atlassian.net` first) + a one-time model download (`huggingface.co`) and accepted Atlassian as a real read-only vendor. All other no-egress / vendors=none assumptions still hold (default-deny, BR-013); any further vendor/paid-tier/egress target re-fires Rule 3 → CEO.

---

HANDOFF
feature: mcp-data-platform
status: done
artifacts: [requirements.md]
counts: FR=22 AC=62 NFR=12
blocking: - none
notes:
  - NEW FRs added (in place, no renumber): FR-016 hybrid grounded search, FR-017 Jira source #10, FR-018 live-vs-knowledge/freshness/conflict/authority, FR-019 permission server-side before assembly, FR-020 versioning/entities-CTE/summaries, FR-021 B4 grounding verdict+provenance, FR-022 gateway-boundary in-process. Base FR-001..FR-015 unchanged.
  - New AC count = 26 across FR-016..FR-022 (FR-021 has 6, incl. adversarial AC-001 no-source→UNKNOWN, AC-002 source→FACT w/ resolvable evidence, AC-003 conflict→CONFLICT exposed; mapped to GT-1..GT-7 of ADR-0018). FR-019 AC-002/AC-003 = L-001 adversarial (restricted doc not candidate/not cited, excluded BEFORE assembly). Read-only invariant as regression ACs on new surface (0 write tool, write rejected): FR-016 AC-004, FR-017 AC-003, FR-022 AC-003 + NFR-006.
  - Traceability added: Must→FR, FR→tool/ADR table, and GT→FR-021 AC map (for QA-plan). All 13 new tools covered: 8 Knowledge (search_company_knowledge→FR-016; get_service/get_repository→FR-020; search_code→FR-016/FR-020; get_jira_context→FR-018; find_related_knowledge/get_knowledge_summary/get_document_version→FR-020) + 5 Jira (jira_*→FR-017).
  - uncovered tools: NONE — every new operationId in api-contract.yaml (x-change:CHG-001) maps to ≥1 FR.
  - TBD-with-reason (no invented numbers, L-002): NFR-010 τ thresholds, NFR-007 latency budget, open questions 6/7/8 — all blocked by HF egress / unmeasurable pipeline; non-blocking for build. calibration_status=uncalibrated carried in envelope.
  - For LEAD: FR→epic alignment is E1..E8 in architecture.md; FR-021 (E8 grounding gate) MUST sit after FR-019 (E6 permission) on the path. FR-019 adversarial + FR-021 GT-1..GT-7 are the sign-off gates.
  - For QA-plan: FR-021 AC-001/AC-003 (adversarial) and FR-019 AC-002 (restricted-doc) are the highest-priority negative cases; GT-5/GT-7 are structural single-choke-point/provenance tests (NFR-008). Grounded-envelope invariants assert via assert_grounding_invariants() alongside GT-1..GT-7.

---

HANDOFF
feature: mcp-data-platform
status: done
artifacts: [requirements.md]
counts: FR=27 AC=79 NFR=14
blocking: - none
notes:
  - CHG-003 extension (Option B, lean; ADR-0023 proposed; D-006). NEW FRs appended in place, no renumber of FR-001..022: FR-023 real Confluence Cloud ingestion via CLI (Confluence first, generalises to GitLab/OpenSearch/Jira), FR-024 default-deny egress + source-host allow-list (+ huggingface.co), FR-025 least-privilege read-only Atlassian credential (never leaked; doctor refuses write-capable account), FR-026 CLI operability for all 9 sources (4 ingestable + 5 live-only runbook), FR-027 real embedding-model download enabling NFR-003 measurement (Should). Base + CHG-001 FRs unchanged.
  - NEW NFRs: NFR-013 egress default-deny guarantee (100% unlisted-host refusal, 0 server ports), NFR-014 read-only credential guarantee (0 token leaks, 100% write-capable-account refusal) + honest NFR-003-enablement note.
  - New AC count = 17 across FR-023..FR-027 (FR-023=4, FR-024=3, FR-025=3, FR-026=4, FR-027=3). Total FR AC = 62 (CHG-001 run) + 17 = 79.
  - Adversarial/invariant ACs mapped 1:1 to ADR-0023 §6e: #1 egress default-deny proven → FR-024 AC-002; #2 allow-list honoured → FR-024 AC-001; #3 token never leaked → FR-025 AC-002; #4 server read-only/stdio unchanged → FR-023 AC-003 / FR-024 AC-003 / FR-025 AC-003 / FR-026 AC-004. Each ties to NFR-013/NFR-014.
  - CLI operability (FR-026): per-class ACs — ingestable (doctor→ingest→status→verify via kb_semantic_search) AC-001; live-only (doctor→register→tools/list, zero write tools, never ingested) AC-002/AC-003; read-only across all 9 + Jira AC-004.
  - L-002 honesty kept: FR-027 AC-003 + NFR-014 state that opening HF egress ENABLES NFR-003 measurement but does NOT prove quality; recall number + FACT↔LOW τ remain TBD-with-reason (real golden-set bake-off / spike S2 is a later eval task, not an AC here); no number invented; calibration_status=uncalibrated carried. New TBD-with-reason items: open questions 9 (real recall/τ) and 10 (GitLab/OpenSearch/Jira hosts + scopes), both non-blocking.
  - Traceability: full coverage of plan-approval.md CHG-003 approved scope (items 1-4 + kept invariants) → FR table added; FR/AC → ADR-0023 §6e test table added; Must→FR rows added. uncovered = [] (none) — every CHG-003 scope item and kept invariant maps to ≥1 FR/AC.
  - EB-006 added to Existing behaviour: egress/vendor boundary relaxed ONLY for ingest pull + model download; read-only-to-source, stdio/no-new-port, secret-handling, grounding/permission choke points carried forward as regression ACs.
  - BR-013..BR-016 added: default-deny egress allow-list; read-only-to-source preserved; least-privilege read-only credential never leaked; model egress separable + NFR-003 enabled-not-proven.
  - Data & privacy + Out of scope extended for CHG-003 (real egress/credential surface; HF-egress separable; live-only never ingested; no write-back/new port; do not run runbook with real token before Gate-1 recorded).
  - For LEAD: FR-023..FR-027 are a lean operational/boundary change on built capability — align to the ADR-0023 work items (egress allow-list guard + its adversarial test, doctor read-only check for Confluence Cloud live, the 9-source runbook). FR-024/FR-025 adversarial tests (unlisted-host-refused, token-never-leaked) + FR-023/FR-026 AC-003/AC-004 (server read-only/stdio unchanged) are the sign-off gates.
  - For QA-plan: highest-priority negatives = FR-024 AC-002 (unlisted host refused, L-001 single choke point), FR-025 AC-002 (forced-error on credential path → token absent both ways), FR-025 AC-003 (write-capable account refused), FR-023 AC-002 (unreachable source → partial, no fabrication). FR-027 AC-003 is an honesty assertion (no invented recall), not a quality measurement.
next_role: squad-lead
check_sh: scripts/squad/check.sh requirements docs/squad/features/mcp-data-platform
