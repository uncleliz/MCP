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
