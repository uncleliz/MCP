# ADR-0001: uv workspace monorepo, một package cho mỗi MCP server

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa), chờ xác nhận Gate B về vị trí thư mục

## Context

Feature gồm 9 MCP server độc lập + 1 ingest pipeline, tất cả chạy Python, chia sẻ rất nhiều
pattern chung (load config, HTTP client retry, map lỗi, chuẩn hoá nội dung, dựng citation).
Repo đã có scaffold `packages/mcp_<source>/{src,tests}` cho 9 nguồn và `packages/mcp_common`.
Nếu mỗi server là một repo riêng thì 9 lần copy-paste lớp chung; nếu tất cả nằm trong một
package đơn lẻ thì không thể cài/chạy riêng từng server và mọi dependency (boto3, kafka,
redis, psycopg, opensearch-py) bị kéo vào mọi process.

## Decision

Dùng một **uv workspace** ở gốc repo: `pyproject.toml` gốc khai báo
`[tool.uv.workspace] members = ["packages/*"]`, mỗi MCP server là một package riêng
(`mcp-confluence`, `mcp-gitlab`, …, `mcp-pgvector`, `mcp-ingest`) phụ thuộc vào package
chung `mcp-common`; mỗi package có một console script entrypoint riêng và tập dependency
riêng. Giữ thư mục `packages/` ở gốc repo (không lồng dưới `backend/`) vì đây là repo
backend-only và scaffold hiện có đã ở đó.

## Alternatives Considered

### Alternative 1: Một package duy nhất `mcp_platform` với 9 entrypoint
- **Pros**: Cấu hình đơn giản nhất, không cần workspace.
- **Cons**: Mọi process phải cài toàn bộ dependency của 9 nguồn; một lỗi dependency của
  Kafka làm chết cả server Confluence; không thể version/rollout từng phase độc lập.
- **Why not**: Phá vỡ tính độc lập giữa các server mà PRD yêu cầu và làm rollout theo phase khó kiểm soát.

### Alternative 2: 10 repo riêng (polyrepo)
- **Pros**: Cách ly tuyệt đối, release riêng.
- **Cons**: Lớp chung phải publish thành package nội bộ; 10 pipeline CI; thay đổi contract
  phải đồng bộ qua 10 repo.
- **Why not**: Chi phí vận hành quá cao cho một team nhỏ ở v1.

### Alternative 3: Poetry / Hatch workspace thay cho uv
- **Pros**: Poetry phổ biến lâu hơn.
- **Cons**: Poetry chưa có workspace bản chính thức tương đương; uv 0.11 đã có sẵn trên máy dev và nhanh hơn nhiều.
- **Why not**: uv đã cài (0.11.7) và hỗ trợ workspace + lockfile duy nhất.

## Consequences

### Positive
- Một `uv.lock` duy nhất cho toàn repo → dependency nhất quán, CI cache tốt.
- `uv run --package mcp-confluence mcp-confluence` chạy đúng một server với đúng dependency.
- Lớp chung `mcp-common` được test một lần, dùng cho cả 10 thành phần.

### Negative
- Thay đổi `mcp-common` có bán kính ảnh hưởng rộng → phải có test suite riêng và semver nội bộ.
- Layout `packages/` lệch khỏi mô tả `backend/` trong CLAUDE.md → cần orchestrator cho phép
  `squad-backend` ghi vào `packages/` (điểm cần xác nhận ở Gate B).

### Risks
- Dependency nặng (confluent-kafka, sentence-transformers) làm `uv sync` toàn workspace chậm.
  Giảm thiểu: khai báo chúng trong extras/optional group của đúng package, CI chỉ sync package đang test.
