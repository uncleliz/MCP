# ADR-0013: OpenAPI 3.1 làm contract cho MCP tool + luật tương thích

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

Feature không có HTTP API và không có frontend, nhưng vẫn cần **một artifact contract duy nhất,
kiểm chứng được bằng máy** giữa SA (thiết kế), Lead (chia task), Backend (implement) và QA
(verify) — theo skill `contract-first`. Bề mặt công khai ở đây là *tool interface của MCP*:
tên tool, JSON Schema input, JSON Schema output. MCP tự thân dùng JSON Schema cho
`inputSchema`/`outputSchema`, còn workflow squad quy định file `api-contract.yaml`.

## Decision

Viết `docs/squad/mcp-data-platform/api-contract.yaml` dưới dạng **OpenAPI 3.1**, trong đó:

- Mỗi MCP tool là một operation `POST /mcp/{server}/tools/{tool_name}` với `operationId` =
  đúng tên tool MCP, `requestBody` = `inputSchema`, response `200` = `outputSchema`.
  HTTP path/verb ở đây là **cách biểu diễn**, không phải endpoint tồn tại thật ở v1 (ghi rõ ở
  `info.description`); nhưng chính cách biểu diễn này là đường mở sang HTTP transport của BR-004.
- `components.schemas` chứa `ToolResult` envelope (ADR-0004), `Citation`, `Meta`, `Error`
  (một schema lỗi duy nhất dùng chung cho mọi operation) và schema item của từng nguồn.
- Mọi operation có `x-requirements: [FR-xxx]`, `x-phase`, `x-mcp-server`, `x-readonly: true`,
  `x-side-effects: none`, và `examples` cho cả trường hợp `ok` và `empty`.
- Ingest pipeline không có tool MCP → biểu diễn bằng operation với `x-interface: cli` và
  `x-cli-command`, schema input = tham số CLI, schema output = JSON run report.
- **Kiểm chứng hai chiều**:
  - Backend: mỗi server sinh `tools.snapshot.json` (tool name + inputSchema + outputSchema) và
    một test so nó với contract → contract drift làm test đỏ.
  - QA: validate `structuredContent` thật do tool trả về theo schema trong contract (jsonschema),
    cho cả nhánh `ok`, `empty`, `not_found`, `error`.
- **Luật tương thích** (`info.version` theo semver):
  - Minor/tương thích: thêm tool mới, thêm field optional vào output, thêm field optional vào
    input, thêm giá trị enum vào field output không bắt buộc-exhaustive.
  - Major/breaking: đổi tên/bỏ tool, bỏ hoặc đổi kiểu field output, biến input optional thành
    required, siết validation input, bỏ giá trị enum.
  - Mọi breaking change sau Gate B đưa flow quay lại stage `sa` (theo luật trong CLAUDE.md).

## Alternatives Considered

### Alternative 1: Chỉ dùng JSON Schema rời cho từng tool
- **Pros**: Sát nhất với MCP, không có khái niệm HTTP giả.
- **Cons**: Không có một file đơn để review/diff; không mô tả được quan hệ tool ↔ server ↔ FR;
  workflow squad quy ước một file `api-contract.yaml`.
- **Why not**: Mất tính "một artifact duy nhất" mà contract-first yêu cầu. (Đã bù: JSON Schema
  của từng tool vẫn nằm nguyên trong `components.schemas` và trích xuất được bằng script.)

### Alternative 2: AsyncAPI
- **Pros**: Mô tả message-driven đúng bản chất JSON-RPC hơn.
- **Cons**: Tooling Python yếu hơn, team ít quen, không thêm khả năng kiểm chứng nào.
- **Why not**: Chi phí học không đổi lấy lợi ích.

### Alternative 3: Tự sinh contract từ code sau khi implement
- **Pros**: Không bao giờ lệch.
- **Cons**: Contract-after-implementation không điều phối được công việc song song và không
  chặn được thay đổi phá vỡ (anti-pattern nêu rõ trong skill contract-first).
- **Why not**: Ngược nguyên tắc; chiều đúng là code phải khớp contract, và snapshot test giữ điều đó.

## Consequences

### Positive
- Lead có một bảng tool đầy đủ để chia task; QA có schema để assert; Backend có `outputSchema`
  copy trực tiếp vào SDK.
- Mọi thay đổi bề mặt tool là một diff trên một file duy nhất → review được.

### Negative
- Biểu diễn HTTP là "giả" ở v1 → phải nêu rõ để người đọc không đi tìm endpoint.
- Không có OpenAPI linter cài trên máy (redocly/spectral đều không có) → v1 chỉ validate được
  bằng `python -c "import yaml"` + một script kiểm tra cấu trúc; nên bổ sung
  `check-jsonschema`/`openapi-spec-validator` vào dev dependency.

### Risks
- Contract phình to (≈ 45 tool). Giảm thiểu: dùng `$ref` triệt để, item schema tách riêng theo
  nguồn, và `x-phase` để đọc theo từng phase.
