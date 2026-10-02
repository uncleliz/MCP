# Architecture Decision Records

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-uv-workspace-monorepo.md) | uv workspace monorepo, một package cho mỗi MCP server | accepted | 2026-10-01 |
| [0002](0002-mcp-python-sdk-stdio-transport.md) | MCP Python SDK chính thức, stdio cho v1 với bootstrap transport-agnostic | accepted | 2026-10-01 |
| [0003](0003-read-only-defense-in-depth.md) | Read-only theo chiều sâu (defense in depth) trên cả 9 nguồn | accepted | 2026-10-01 |
| [0004](0004-uniform-tool-result-envelope.md) | Envelope kết quả tool thống nhất, citation bắt buộc, empty là status | accepted | 2026-10-01 |
| [0005](0005-config-secrets-and-stderr-logging.md) | Config/secret qua env + pydantic-settings, log JSON chỉ ra stderr | accepted | 2026-10-01 |
| [0006](0006-http-client-and-timeout-budget.md) | httpx + tenacity và timeout budget chuẩn (chốt ngưỡng NFR-002) | accepted | 2026-10-01 |
| [0007](0007-thin-rest-clients-confluence-gitlab.md) | Thin REST client tự viết cho Confluence & GitLab | accepted | 2026-10-01 |
| [0008](0008-per-source-sdk-selection.md) | Chọn SDK cho OpenSearch, AWS, Redis, Postgres/pgvector | accepted | 2026-10-01 |
| [0009](0009-kafka-client-and-readonly-consumption.md) | Kafka client + giao thức tiêu thụ read-only (`confluent-kafka`, spike S4) | accepted | 2026-10-01 |
| [0010](0010-embedding-provider-abstraction.md) | Embedding provider abstraction + model/dimension mặc định (provisional `BAAI/bge-m3`, chờ S2) | proposed | 2026-10-01 |
| [0011](0011-pgvector-schema-and-upsert.md) | Schema pgvector, HNSW cosine, upsert idempotent theo content hash | accepted | 2026-10-01 |
| [0012](0012-ingest-pipeline-as-cli.md) | Ingest/embedding pipeline là CLI độc lập, scheduler ngoài | accepted | 2026-10-01 |
| [0013](0013-openapi-as-mcp-tool-contract.md) | OpenAPI 3.1 làm contract cho MCP tool + luật tương thích | accepted | 2026-10-01 |
| [0014](0014-mcp-prompts-for-cross-source-synthesis.md) | MCP Prompts làm cơ chế tổng hợp đa nguồn & kỷ luật citation | accepted | 2026-10-01 |
| [0015](0015-untrusted-content-and-redaction.md) | Xử lý nội dung không tin cậy: chống prompt injection + redaction secret | accepted | 2026-10-01 |
| [0016](0016-document-visibility-and-future-rbac.md) | Cột `visibility` và đường mở sang RBAC per-user khi chuyển remote (corpus team-only, default-deny S5) | accepted | 2026-10-01 |
| [0017](0017-chg001-company-knowledge-deviation.md) | CHG-001 Company Knowledge layer lệch khỏi baseline (deviation) — **accepted (CEO Gate 1): Option C** | accepted | 2026-10-01 |
| [0018](0018-grounding-evidence-contract.md) | Grounding/Evidence contract — company fact phải có evidence, không evidence → UNKNOWN (confidence deterministic D1; ngưỡng τ chờ eval) | proposed | 2026-10-01 |
| [0019](0019-jira-source-thin-rest.md) | Jira nguồn #10 — thin REST read-only, flavor Cloud/Server split (CHG-001) | accepted | 2026-10-01 |
| [0020](0020-hybrid-rag-reranker-local.md) | Hybrid-RAG trong một Postgres — tsvector+pgvector+RRF+reranker local offline + compression (CHG-001) | proposed | 2026-10-01 |
| [0021](0021-gateway-boundary-in-process.md) | Company MCP gateway-boundary in-process (giữ stdio) — routing/permission/audit/rate-limit (CHG-001) | accepted | 2026-10-01 |
| [0022](0022-knowledge-domains-cte-migration-locking.md) | 4 domain Company Knowledge + recursive CTE + migration-locking DK2/DK3 (CHG-001) | accepted | 2026-10-01 |
