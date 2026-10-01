# Company Knowledge MCP — Technical Specification

**Version:** 1.0  
**Status:** Proposed  
**Language:** Python  
**Primary LLM:** Claude  
**Primary Knowledge Store:** PostgreSQL + pgvector  
**Deployment:** Docker / Docker Compose  
**Sources:** GitLab, Jira, Confluence

---

## 1. Executive Summary

Build a company-wide AI knowledge platform where Claude can answer questions using company knowledge collected from GitLab, Jira, and Confluence.

The system must not depend on the source systems being available at query time.

The architecture therefore separates:

1. **Source Systems** — GitLab, Jira, Confluence
2. **Ingestion Layer** — continuously synchronizes source knowledge
3. **Company Knowledge Store** — PostgreSQL + pgvector
4. **Retrieval Engine** — Hybrid RAG
5. **Knowledge MCP** — exposes company-level knowledge tools
6. **Live MCP** — accesses current source-system information when required
7. **Company MCP Gateway** — unified MCP boundary
8. **Claude** — reasoning and answer generation

Core architecture:

```text
                              ┌──────────────┐
                              │    Claude    │
                              │     LLM      │
                              └──────┬───────┘
                                     │
                                     ▼
                         ┌──────────────────────┐
                         │   COMPANY MCP        │
                         │      GATEWAY         │
                         └──────────┬───────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
          Knowledge MCP        Live MCP            Action MCP
                 │                  │                  │
                 │           ┌──────┼──────┐           │
                 │           ▼      ▼      ▼           │
                 │        GitLab   Jira  Confluence    │
                 │
                 ▼
          ┌───────────────────────┐
          │   Retrieval Engine    │
          │                       │
          │ Vector Search         │
          │ Full Text Search      │
          │ Metadata Filtering    │
          │ Relationship Search   │
          │ RRF                   │
          │ Reranking             │
          │ Context Compression   │
          └───────────┬───────────┘
                      │
                      ▼
          ┌─────────────────────────┐
          │ Company Knowledge Store │
          │                         │
          │ PostgreSQL              │
          │ pgvector                │
          │                         │
          │ Documents               │
          │ Chunks                  │
          │ Embeddings              │
          │ Entities                │
          │ Relationships           │
          │ Summaries               │
          │ Versions                │
          │ Permissions             │
          └────────────▲────────────┘
                       │
                       │ ingestion
              ┌────────┼────────┐
              │        │        │
            GitLab    Jira   Confluence
```

---

# 2. Goals

## 2.1 Primary Goals

- Centralize company knowledge into a local persistent knowledge store.
- Allow Claude to answer questions without querying GitLab/Jira/Confluence for every request.
- Continue answering from the latest synchronized snapshot when source systems are temporarily unavailable.
- Support semantic, keyword, metadata, and relationship-based retrieval.
- Minimize the amount of context sent to Claude.
- Preserve source provenance and timestamps.
- Support historical versions of knowledge.
- Preserve source permissions.
- Allow live verification against source systems when freshness matters.
- Expose company knowledge through MCP.
- Keep the architecture extensible to future AI agents and actions.

## 2.2 Secondary Goals

- Support code knowledge from GitLab.
- Support documentation knowledge from Confluence.
- Support project/issue knowledge from Jira.
- Connect related knowledge across systems.
- Provide observability for ingestion, retrieval, MCP, and LLM operations.
- Run locally using Docker Compose for development.

---

# 3. Non-Goals

The initial version will NOT:

- Train or fine-tune Claude.
- Replace GitLab, Jira, or Confluence.
- Build a new vector database.
- Allow arbitrary SQL execution by Claude.
- Automatically execute destructive actions.
- Require source systems to be online during every query.
- Build a fully autonomous multi-agent platform in V1.

---

# 4. Architecture Principles

## 4.1 Source of Truth

GitLab, Jira, and Confluence remain source systems of record.

The Company Knowledge Store is a synchronized knowledge snapshot optimized for AI retrieval.

```text
Source Systems
      │
      │ synchronize
      ▼
Company Knowledge Store
      │
      │ retrieve
      ▼
Claude
```

## 4.2 MCP Is an Access Layer

MCP is not the knowledge database.

MCP provides controlled tools/resources to Claude.

```text
Claude
  ↓
MCP
  ↓
Knowledge / Live / Action Services
```

## 4.3 RAG Is the Retrieval Mechanism

RAG is responsible for finding relevant context.

```text
Question
   ↓
Retrieval
   ↓
Relevant Knowledge
   ↓
Context Pack
   ↓
Claude
```

## 4.4 PostgreSQL Is the Company Knowledge Store

PostgreSQL stores:

- source metadata
- documents
- document versions
- chunks
- embeddings
- entities
- relationships
- summaries
- permissions
- synchronization state

pgvector provides vector retrieval.

PostgreSQL full-text capabilities provide keyword retrieval.

## 4.5 Never Send the Entire Knowledge Base to Claude

The system must retrieve and compress context before invoking Claude.

```text
Large Knowledge Base
       ↓
Retrieval
       ↓
Reranking
       ↓
Compression
       ↓
Small Context Pack
       ↓
Claude
```

---

# 5. System Components

## 5.1 Claude

Responsibilities:

- Understand user intent.
- Decide whether knowledge retrieval is required.
- Select MCP tools.
- Reason over retrieved context.
- Determine whether live verification is required.
- Produce final answer.
- Cite knowledge sources supplied by the MCP layer.

Claude is not responsible for:

- storing company knowledge
- indexing documents
- generating the complete company database
- direct arbitrary SQL

---

# 6. Company MCP Gateway

The gateway is the primary MCP boundary exposed to AI clients.

Responsibilities:

- MCP routing
- authentication
- authorization
- tool discovery
- policy enforcement
- audit logging
- request tracing
- rate limiting
- routing to Knowledge MCP / Live MCP / Action MCP

Logical structure:

```text
Claude
  │
  ▼
Company MCP Gateway
  │
  ├── Knowledge MCP
  ├── Live MCP
  └── Action MCP
```

The gateway should not contain business knowledge itself.

---

# 7. Knowledge MCP

Knowledge MCP exposes company-level semantic tools.

## 7.1 Required Tools

### search_company_knowledge

Purpose:

Search across company knowledge.

Input:

```json
{
  "query": "How does payment timeout work?",
  "sources": ["gitlab", "jira", "confluence"],
  "service": "payment-service",
  "top_k": 10
}
```

Output:

```json
{
  "results": [
    {
      "chunk_id": "123",
      "document_id": "456",
      "title": "Payment Architecture",
      "content": "...",
      "score": 0.92,
      "source": "confluence",
      "url": "...",
      "updated_at": "2026-09-30T03:15:00Z"
    }
  ]
}
```

### get_document

Retrieve a specific document.

### get_document_version

Retrieve a historical version.

### get_service

Retrieve consolidated knowledge about a service.

Example:

```json
{
  "service": "payment-service"
}
```

Expected result:

```text
Service overview
Repository
Owner team
Architecture
Dependencies
Related Jira
Documentation
Recent changes
Known issues
```

### get_repository

Retrieve repository-level knowledge.

### search_code

Search indexed code using semantic + keyword retrieval.

### get_jira_context

Retrieve local synchronized Jira context.

### find_related_knowledge

Traverse relationships.

Example:

```text
Payment Service
  ↓
depends_on
  ↓
Wallet Service
```

### get_knowledge_summary

Return consolidated knowledge rather than raw chunks.

---

# 8. Live MCP

Live MCP is used only when current source information matters.

Examples:

- current Jira status
- latest GitLab branch
- latest merge request
- latest commit
- current Confluence page
- current deployment metadata

Architecture:

```text
Claude
  ↓
Company MCP Gateway
  ↓
Live MCP
  ├── GitLab
  ├── Jira
  └── Confluence
```

The live layer is optional for most historical/knowledge questions.

---

# 9. Hybrid RAG

The retrieval engine must use multiple retrieval signals.

## 9.1 Retrieval Signals

### A. Vector Search

Uses embeddings to find semantic similarity.

Example:

```text
Query:
"payment request hangs"

Can match:

"Payment transaction timeout handling"
```

even when exact keywords differ.

### B. Keyword Search

Useful for:

- class names
- service names
- Jira IDs
- repository names
- error codes
- configuration keys
- API paths

Example:

```text
PAY-123
PaymentService
payment.timeout
```

### C. Metadata Filtering

Examples:

```text
source = gitlab
service = payment
language = java
team = payment
environment = production
```

### D. Relationship Retrieval

Example:

```text
Payment Service
  ├── repository → payment-service
  ├── documented_by → Payment Architecture
  ├── related_to → PAY-123
  └── depends_on → Wallet Service
```

---

# 10. Retrieval Pipeline

```text
User Question
      │
      ▼
Query Analysis
      │
      ├───────────────┐
      ▼               ▼
Vector Search      Keyword Search
      │               │
      └───────┬───────┘
              ▼
        Metadata Filter
              │
              ▼
        Relationship Search
              │
              ▼
          RRF Merge
              │
              ▼
           Reranker
              │
              ▼
        Top N Chunks
              │
              ▼
      Context Compression
              │
              ▼
         Context Pack
              │
              ▼
            Claude
```

---

# 11. RRF

Use Reciprocal Rank Fusion to combine retrieval strategies.

Conceptually:

```text
Vector ranking
+
Keyword ranking
+
Relationship ranking
=
Unified ranking
```

The implementation should not rely on vector similarity alone.

---

# 12. Reranking

After initial retrieval, use a reranker to reorder candidates.

Example:

```text
Initial retrieval:
100 chunks

      ↓

Reranker

      ↓

20 high-quality chunks
```

The reranker should consider:

- semantic relevance
- exact keyword matches
- document type
- source
- service
- freshness
- relationship relevance

---

# 13. Context Compression

The system must reduce context before sending it to Claude.

Flow:

```text
100 retrieved chunks
        ↓
Reranker
        ↓
20 chunks
        ↓
Context compressor
        ↓
5-10 knowledge blocks
        ↓
Claude
```

Compression must preserve:

- factual statements
- source references
- timestamps
- document IDs
- URLs
- confidence
- important technical details

Never compress away provenance.

---

# 14. Context Pack

The retrieval layer should generate a structured context package.

Example:

```json
{
  "query": "How does payment timeout work?",
  "facts": [
    "Connection timeout is 3 seconds.",
    "Read timeout is 5 seconds.",
    "The service retries twice."
  ],
  "services": [
    "payment-service"
  ],
  "related_jira": [
    "PAY-121"
  ],
  "sources": [
    {
      "title": "Payment Architecture",
      "source": "confluence",
      "updated_at": "2026-09-30T03:15:00Z",
      "url": "..."
    }
  ],
  "knowledge_timestamp": "2026-09-30T03:15:00Z"
}
```

This structure is what gets passed to Claude.

---

# 15. Company Knowledge Store

PostgreSQL should contain the following logical domains.

```text
PostgreSQL
│
├── sources
├── documents
├── document_versions
├── document_chunks
├── entities
├── relationships
├── knowledge_summaries
├── permissions
├── ingestion_jobs
└── sync_cursors
```

---

# 16. Core Database Schema

## 16.1 sources

```sql
CREATE TABLE sources (
    id BIGSERIAL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    base_url TEXT,
    status VARCHAR(30),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Supported source types:

```text
gitlab
jira
confluence
```

---

# 17. documents

```sql
CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(id),
    external_id VARCHAR(500) NOT NULL,
    document_type VARCHAR(100),
    title TEXT,
    url TEXT,
    repository VARCHAR(500),
    path TEXT,
    language VARCHAR(100),
    service VARCHAR(255),
    metadata JSONB,
    content_hash VARCHAR(128),
    current_version_id BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE(source_id, external_id)
);
```

---

# 18. document_versions

```sql
CREATE TABLE document_versions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    version VARCHAR(255),
    commit_id VARCHAR(255),
    content TEXT NOT NULL,
    content_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Purpose:

- preserve historical knowledge
- support rollback
- support audit
- allow historical questions

---

# 19. document_chunks

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    version_id BIGINT NOT NULL REFERENCES document_versions(id),
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    token_count INT,
    metadata JSONB,
    embedding VECTOR(1536),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

The embedding dimension must match the selected embedding model.

---

# 20. Vector Index

For appropriate pgvector versions and workload:

```sql
CREATE INDEX idx_document_chunks_embedding
ON document_chunks
USING hnsw (embedding vector_cosine_ops);
```

The exact index strategy must be benchmarked using real company data.

---

# 21. Entities

Entities represent important company objects.

Examples:

- service
- repository
- team
- person
- Jira issue
- API
- database
- topic
- documentation page

Schema:

```sql
CREATE TABLE entities (
    id BIGSERIAL PRIMARY KEY,
    entity_type VARCHAR(100) NOT NULL,
    external_id VARCHAR(500),
    name VARCHAR(500) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 22. Relationships

```sql
CREATE TABLE relationships (
    id BIGSERIAL PRIMARY KEY,
    source_entity_id BIGINT NOT NULL REFERENCES entities(id),
    relationship_type VARCHAR(100) NOT NULL,
    target_entity_id BIGINT NOT NULL REFERENCES entities(id),
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Examples:

```text
payment-service
    ├── depends_on → wallet-service
    ├── documented_by → payment-architecture
    ├── related_to → PAY-121
    └── owned_by → payment-team
```

---

# 23. Knowledge Summaries

Store consolidated knowledge for high-value entities.

```sql
CREATE TABLE knowledge_summaries (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES entities(id),
    summary_type VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    source_version BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

Examples:

```text
service_overview
architecture_summary
known_issues
dependency_summary
team_summary
repository_summary
```

---

# 24. Permissions

Knowledge retrieval must respect source permissions.

```sql
CREATE TABLE document_permissions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    principal_type VARCHAR(50) NOT NULL,
    principal_id VARCHAR(255) NOT NULL,
    permission VARCHAR(50) NOT NULL
);
```

Do not expose documents to users who cannot access the corresponding source information.

Permission filtering should happen before final context assembly.

---

# 25. Ingestion Architecture

```text
GitLab
   │
Jira ────────┐
   │         │
Confluence ──┘
             │
             ▼
        Source Connectors
             │
             ▼
          Normalize
             │
             ▼
         Deduplicate
             │
             ▼
           Version
             │
             ▼
           Chunk
             │
             ▼
    Contextual Enrichment
             │
       ┌─────┼─────┐
       ▼     ▼     ▼
   Metadata Entity Relations
       │     │     │
       └─────┼─────┘
             ▼
         Embedding
             │
             ▼
       PostgreSQL
```

---

# 26. Ingestion Principles

## 26.1 Idempotency

Same source version must not create duplicate knowledge.

Use:

```text
source_id
external_id
content_hash
version
```

to detect unchanged content.

## 26.2 Incremental Sync

Do not re-ingest everything on every run.

Use:

- Git commit / branch cursor
- Jira updated timestamp
- Confluence version/update timestamp

## 26.3 Snapshot Retention

Keep enough historical versions to answer:

> "What did we know about this service last month?"

---

# 27. GitLab Ingestion

Capture:

- repository
- branch
- file path
- language
- commit SHA
- code
- README
- configuration
- API definitions
- architecture documents
- merge request metadata

Potential entities:

```text
Repository
File
Class
Service
API
Team
Merge Request
Commit
```

---

# 28. Jira Ingestion

Capture:

- issue key
- title
- description
- comments
- status
- priority
- labels
- project
- assignee
- reporter
- linked issues
- timestamps

Potential entities:

```text
Jira Issue
Project
Team
Person
Service
```

---

# 29. Confluence Ingestion

Capture:

- page ID
- title
- space
- content
- parent page
- labels
- author
- updated timestamp
- page hierarchy

Potential entities:

```text
Confluence Page
Space
Architecture
Service
Team
```

---

# 30. Source Normalization

All source data should be transformed into an internal canonical document format.

Example:

```python
class KnowledgeDocument:
    source_type: str
    external_id: str
    document_type: str
    title: str
    url: str | None
    content: str
    metadata: dict
    version: str
    updated_at: datetime
```

This prevents the retrieval layer from depending directly on GitLab/Jira/Confluence APIs.

---

# 31. Chunking Strategy

Chunking must be document-type aware.

## Code

Prefer semantic units:

```text
repository
 → file
   → class
     → method
```

Do not blindly split Java files every N characters.

## Confluence

Prefer:

```text
page
 → heading
   → paragraph/list/table
```

## Jira

Prefer:

```text
issue
 → description
 → comments
 → acceptance criteria
 → linked issues
```

---

# 32. Embedding Strategy

Embeddings should be generated for chunks and optionally for consolidated knowledge summaries.

Recommended:

```text
Chunk embedding
Summary embedding
```

Do not embed every tiny metadata field separately.

The embedding model must be selected and benchmarked against real company queries.

---

# 33. Metadata Strategy

Every chunk should contain useful retrieval metadata.

Example:

```json
{
  "source": "gitlab",
  "repository": "payment-service",
  "path": "src/main/java/PaymentService.java",
  "service": "payment-service",
  "language": "java",
  "team": "payment-team",
  "branch": "main",
  "commit": "abc123"
}
```

Metadata is used for:

- filtering
- ranking
- authorization
- provenance
- freshness

---

# 34. Freshness Model

Every knowledge item must have:

```text
created_at
updated_at
source_updated_at
synced_at
version
```

The system must distinguish:

```text
Source current time
Knowledge synchronization time
Document modification time
```

---

# 35. Offline-Source Behavior

If GitLab/Jira/Confluence is unavailable:

```text
User
 ↓
Claude
 ↓
Knowledge MCP
 ↓
Local PostgreSQL
 ↓
RAG
 ↓
Answer
```

The answer must clearly indicate that it is based on the local snapshot when freshness is relevant.

Example:

```text
Knowledge snapshot:
2026-09-30 03:15 UTC

Live verification:
Unavailable
```

Do not pretend the local snapshot is realtime.

---

# 36. Live Verification Policy

The system should decide whether live verification is necessary.

Examples requiring live verification:

- "currently"
- "now"
- "latest"
- "today"
- "is this Jira ticket still open?"
- "what is the latest commit?"
- "who is currently assigned?"

Examples that normally do not require live verification:

- "How does PaymentService work?"
- "What is the architecture of payment-service?"
- "What happened in PAY-123?"
- "What services depend on Wallet?"

---

# 37. Knowledge vs Live Decision

Conceptually:

```text
Question
   │
   ▼
Intent classification
   │
   ├── Historical / knowledge
   │       ↓
   │   Knowledge MCP
   │
   ├── Current state
   │       ↓
   │   Live MCP
   │
   └── Mixed
           ↓
     Knowledge MCP
           +
       Live MCP
```

---

# 38. Example Mixed Query

User:

> "How does payment timeout work and is PAY-121 still being worked on?"

Flow:

```text
Claude
  │
  ├── search_company_knowledge()
  │        ↓
  │    Payment architecture
  │
  └── Live Jira MCP
           ↓
        PAY-121
           ↓
        Current status
```

Final answer combines:

```text
Historical/company knowledge
+
Current Jira state
```

---

# 39. Provenance

Every retrieved fact should be traceable.

Minimum provenance:

```json
{
  "source": "confluence",
  "document_id": "123",
  "external_id": "456",
  "url": "...",
  "version": "12",
  "source_updated_at": "...",
  "synced_at": "..."
}
```

Claude should receive provenance in the context pack.

---

# 40. Hallucination Control

The retrieval system must support:

- source citations
- freshness timestamps
- confidence/relevance scores
- explicit "not found"
- explicit "source unavailable"
- conflict detection
- version awareness

If no relevant evidence exists:

```text
Do not fabricate an answer.
```

The model should state that the knowledge store does not contain enough evidence.

---

# 41. Conflicting Knowledge

Example:

```text
Confluence:
Timeout = 5 seconds

GitLab config:
Timeout = 10 seconds
```

Do not silently merge them.

The retrieval layer should expose both:

```text
Source A:
5 seconds

Source B:
10 seconds

Different source versions/configuration detected.
```

Claude can explain the conflict and identify which source is newer or more authoritative according to configured source priority.

---

# 42. Source Authority

Define source priority by fact type.

Example:

```text
Runtime/configuration:
GitLab / deployment config

Architecture:
Confluence

Current work status:
Jira

Code behavior:
GitLab
```

This is configurable and must not be hardcoded into the LLM prompt alone.

---

# 43. Security Architecture

```text
User
 ↓
SSO
 ↓
Company MCP Gateway
 ↓
User identity
 ↓
Permission filter
 ↓
Knowledge MCP
 ↓
PostgreSQL
```

Security must be enforced server-side.

Do not rely solely on Claude to avoid unauthorized documents.

---

# 44. MCP Tool Design Rules

Tools should be business-oriented.

Good:

```text
search_company_knowledge
get_service
get_repository
search_code
get_jira_context
find_related_knowledge
```

Avoid:

```text
execute_sql
execute_http
execute_shell
```

especially in V1.

---

# 45. Suggested Python Stack

```text
Python 3.12+

MCP:
FastMCP / MCP Python SDK

API:
FastAPI

Database:
PostgreSQL

Vector:
pgvector

ORM:
SQLAlchemy

Driver:
asyncpg

Migration:
Alembic

HTTP:
httpx

Validation:
Pydantic

Container:
Docker / Docker Compose

Observability:
OpenTelemetry
Grafana

LLM:
Claude / Anthropic API
```

---

# 46. Suggested Repository Structure

```text
company-ai/
│
├── docker-compose.yml
├── .env
├── README.md
│
├── gateway/
│   ├── Dockerfile
│   └── app/
│       ├── main.py
│       ├── auth/
│       ├── routing/
│       ├── policy/
│       └── audit/
│
├── knowledge-mcp/
│   ├── Dockerfile
│   └── app/
│       ├── main.py
│       ├── tools/
│       │   ├── search.py
│       │   ├── documents.py
│       │   ├── services.py
│       │   ├── repositories.py
│       │   └── relationships.py
│       ├── retrieval/
│       │   ├── vector.py
│       │   ├── keyword.py
│       │   ├── metadata.py
│       │   ├── rrf.py
│       │   ├── reranker.py
│       │   └── compressor.py
│       ├── database/
│       └── services/
│
├── live-mcp/
│   ├── Dockerfile
│   └── app/
│       ├── gitlab/
│       ├── jira/
│       └── confluence/
│
├── ingestion/
│   ├── Dockerfile
│   └── app/
│       ├── connectors/
│       │   ├── gitlab.py
│       │   ├── jira.py
│       │   └── confluence.py
│       ├── normalize/
│       ├── chunking/
│       ├── enrichment/
│       ├── embedding/
│       └── pipeline/
│
├── migrations/
│
└── tests/
    ├── retrieval/
    ├── ingestion/
    ├── permissions/
    └── mcp/
```

---

# 47. Docker Architecture

Initial development stack:

```text
docker-compose
│
├── postgres
│   └── PostgreSQL + pgvector
│
├── knowledge-mcp
│
├── live-mcp
│
├── ingestion
│
└── gateway
```

Claude can remain external.

---

# 48. Initial Docker Services

Minimum V1:

```text
postgres
knowledge-mcp
ingestion
```

Then add:

```text
gateway
live-mcp
```

when the base retrieval system is stable.

---

# 49. MVP Implementation Order

## Phase 1

PostgreSQL + pgvector.

Deliver:

- schema
- migrations
- vector index
- metadata indexes

## Phase 2

Knowledge MCP.

Implement:

```text
search_company_knowledge
get_document
```

## Phase 3

Basic RAG.

Implement:

```text
embedding
vector search
```

## Phase 4

Hybrid retrieval.

Add:

```text
keyword search
metadata filtering
RRF
```

## Phase 5

Reranking.

Add reranker and benchmark retrieval quality.

## Phase 6

GitLab ingestion.

Start with:

- repositories
- README
- source code
- configuration
- commits

## Phase 7

Confluence ingestion.

## Phase 8

Jira ingestion.

## Phase 9

Entity + relationship layer.

## Phase 10

Knowledge summaries.

## Phase 11

Live MCP.

## Phase 12

Company MCP Gateway.

## Phase 13

Permission enforcement.

## Phase 14

Observability and evaluation.

---

# 50. Retrieval Evaluation

Do not evaluate the system only by asking whether Claude "sounds good".

Create a test dataset:

```text
Question
Expected source
Expected document
Expected chunk
Expected answer facts
```

Example:

```text
Question:
"What is PaymentService timeout?"

Expected:
payment-service
PaymentService.java
Payment Architecture
```

Metrics:

- Recall@K
- Precision@K
- MRR
- NDCG
- reranker improvement
- answer groundedness
- citation correctness
- latency
- token consumption

---

# 51. MCP Evaluation

Track:

```text
tool selection accuracy
tool call count
failed tool calls
tool latency
retrieval latency
LLM latency
total latency
context tokens
output tokens
```

Goal:

```text
Fewer tool calls
+
Better context
+
Higher answer accuracy
```

---

# 52. Observability

Trace:

```text
User Query
   ↓
Claude
   ↓
MCP Gateway
   ↓
Knowledge MCP
   ↓
Query Analyzer
   ↓
Vector Search
   ↓
Keyword Search
   ↓
RRF
   ↓
Reranker
   ↓
Compression
   ↓
Claude
```

Each stage should have:

- trace ID
- latency
- status
- input size
- output size
- error
- source count

---

# 53. Important Design Decision

Do NOT build:

```text
Claude
 ↓
MCP
 ↓
execute_sql
 ↓
PostgreSQL
```

Build:

```text
Claude
 ↓
Company MCP
 ↓
Knowledge MCP
 ↓
Retrieval Engine
 ↓
PostgreSQL
```

This gives a stable semantic interface and prevents the LLM from becoming dependent on database schema.

---

# 54. Why This Architecture Fits the Requirement

The system has three different concepts:

## Memory

```text
PostgreSQL
+ pgvector
```

Stores company knowledge.

## Retrieval

```text
Hybrid RAG
```

Finds relevant knowledge and compresses context.

## Access

```text
MCP
```

Provides controlled access to Claude.

## Live Context

```text
GitLab/Jira/Confluence MCP
```

Provides current information when needed.

Therefore:

```text
Memory ≠ RAG ≠ MCP ≠ LLM
```

Each layer has a distinct responsibility.

---

# 55. Final Target Architecture

```text
                                ┌──────────────────┐
                                │      Claude      │
                                │       LLM        │
                                └────────┬─────────┘
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │ Company MCP Gateway │
                              └──────────┬──────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
             Knowledge MCP          Live MCP             Action MCP
                    │                    │
                    │             ┌──────┼──────┐
                    │             ▼      ▼      ▼
                    │          GitLab   Jira  Confluence
                    │
                    ▼
             ┌───────────────────┐
             │  Retrieval Engine │
             │                   │
             │ Vector Search     │
             │ Keyword Search    │
             │ Metadata Filter   │
             │ Relationship      │
             │ RRF               │
             │ Reranker          │
             │ Compression       │
             └─────────┬─────────┘
                       │
                       ▼
             ┌─────────────────────┐
             │ Company Knowledge   │
             │ Store               │
             │                     │
             │ PostgreSQL          │
             │ pgvector            │
             │                     │
             │ Documents           │
             │ Versions            │
             │ Chunks              │
             │ Embeddings          │
             │ Entities            │
             │ Relationships       │
             │ Summaries           │
             │ Permissions         │
             └──────────▲──────────┘
                        │
                        │
              ┌─────────┼─────────┐
              │         │         │
              ▼         ▼         ▼
           GitLab     Jira   Confluence
              │         │         │
              └─────────┼─────────┘
                        │
                   Ingestion
```

---

# 56. Target Query Flow

For a normal knowledge question:

```text
1. User asks question
2. Claude identifies knowledge need
3. Claude calls Knowledge MCP
4. Query analyzer determines search strategy
5. Vector search executes
6. Keyword search executes
7. Metadata filters execute
8. Relationship search executes
9. Results are merged using RRF
10. Reranker reorders results
11. Top results are selected
12. Context is compressed
13. Context Pack is generated
14. Claude receives grounded context
15. Claude answers with provenance
```

For a current-state question:

```text
1. User asks current-state question
2. Claude calls Knowledge MCP for baseline
3. Claude identifies need for live verification
4. Claude calls Live MCP
5. Current source state is retrieved
6. Historical + current context are combined
7. Claude answers with freshness information
```

For source outage:

```text
1. User asks question
2. Knowledge MCP searches local PostgreSQL
3. Relevant snapshot is found
4. Live verification is attempted only if needed
5. Live source fails
6. System marks live verification unavailable
7. Claude answers from local snapshot
8. Answer includes snapshot timestamp
```

---

# 57. Future Extensions

The architecture should support future components without changing the knowledge core:

```text
Company AI Platform
│
├── Knowledge Agent
├── Coding Agent
├── Data Agent
├── DevOps Agent
├── Customer Agent
├── Workflow Agent
│
└── Shared Company Knowledge MCP
```

Potential future additions:

- Slack
- Google Drive
- SharePoint
- Notion
- Internal APIs
- Data Warehouse
- Service Catalog
- Incident Management
- Observability systems
- Runbooks
- Architecture Decision Records

---

# 58. V1 Definition of Done

V1 is complete when:

- [ ] PostgreSQL + pgvector runs in Docker.
- [ ] Documents can be stored.
- [ ] Document versions are preserved.
- [ ] Documents can be chunked.
- [ ] Embeddings can be generated.
- [ ] Vector search works.
- [ ] Keyword search works.
- [ ] Hybrid retrieval works.
- [ ] RRF works.
- [ ] Reranking works.
- [ ] Context compression works.
- [ ] Knowledge MCP exposes semantic tools.
- [ ] GitLab ingestion works.
- [ ] Confluence ingestion works.
- [ ] Jira ingestion works.
- [ ] Source provenance is preserved.
- [ ] Local snapshot works without source availability.
- [ ] Live verification can be performed when required.
- [ ] Permission filtering exists.
- [ ] Retrieval evaluation dataset exists.
- [ ] MCP and RAG telemetry exists.

---

# 59. Core Architectural Decision

The final architecture is:

```text
Source Systems
     ↓
Ingestion
     ↓
Company Knowledge Store
     ↓
Hybrid RAG
     ↓
Knowledge MCP
     ↓
Company MCP Gateway
     ↓
Claude
```

with a parallel path:

```text
Claude
  ↓
Company MCP Gateway
  ↓
Live MCP
  ↓
GitLab / Jira / Confluence
```

The central principle is:

> **Build the Company Knowledge Store first. MCP is the interface, RAG is the retrieval engine, and Claude is the reasoning layer.**

This allows the company AI platform to continue answering from synchronized knowledge even when source systems are temporarily unavailable, while still supporting live verification when freshness is required.
