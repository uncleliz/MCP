# ADR-0005: Config/secret qua env + pydantic-settings, log JSON chỉ ra stderr

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

MCP stdio dùng **stdout** làm kênh JSON-RPC: bất kỳ `print()` hay handler logging ghi ra
stdout sẽ làm hỏng phiên kết nối với Claude Desktop/Code — đây là lỗi hạng nhất của server
stdio và ảnh hưởng trực tiếp NFR-005. Đồng thời 9 server cần credential tới hệ thống nội bộ
(Confluence PAT, GitLab PAT, AWS key, Redis ACL, Postgres DSN) và Claude Desktop truyền cấu
hình cho MCP server chủ yếu qua biến môi trường trong `claude_desktop_config.json`.

## Decision

- Cấu hình bằng `pydantic-settings`: một base `CommonSettings` trong `mcp_common.config`
  (timeout, retry, log level, max output bytes) + một `Settings` cho mỗi server với env prefix
  riêng (`MCP_CONFLUENCE_`, `MCP_GITLAB_`, …). Validate lúc khởi động, fail fast với thông báo
  nêu rõ biến còn thiếu.
- Secret: chỉ nhận từ biến môi trường hoặc `*_FILE` trỏ tới file (hỗ trợ `pass`/keychain export),
  kiểu `SecretStr`, **không** đọc file `.env` đã commit; repo chỉ có `.env.example`.
- Logging: `mcp_common.logging.setup()` cấu hình structlog/stdlib JSON handler **chỉ ghi stderr**,
  đồng thời gắn `sys.stdout` vào một guard raise lỗi nếu có ai ghi vào stdout ngoài transport
  (bật trong dev/test). Mọi log đi qua processor redaction (ADR-0015).
- Sinh cấu hình client: `mcp-common config-emit` xuất đoạn JSON cho `claude_desktop_config.json`
  cho từng server đã cài (phục vụ verification NFR-005).

## Alternatives Considered

### Alternative 1: File cấu hình YAML dùng chung cho 9 server
- **Pros**: Một chỗ khai báo mọi nguồn.
- **Cons**: Claude Desktop truyền env, không truyền đường dẫn config theo chuẩn; một file chứa
  mọi secret là tập trung rủi ro; server không cần biết cấu hình của nguồn khác.
- **Why not**: Đi ngược cách MCP client cấu hình server và nguyên tắc least privilege.

### Alternative 2: Secret manager (Vault/1Password CLI) gọi lúc runtime
- **Pros**: Không secret trên đĩa.
- **Cons**: Thêm dependency hạ tầng, tăng thời gian khởi động, không rõ team đã có Vault chưa.
- **Why not**: Ngoài scope v1; hỗ trợ `*_FILE` đã đủ để tích hợp sau.

## Consequences

### Positive
- Sai cấu hình bị phát hiện ngay khi khởi động thay vì lỗi mơ hồ khi gọi tool.
- Ô nhiễm stdout — nguyên nhân hỏng stdio phổ biến nhất — có test tự động chặn.
- Mỗi server chỉ giữ credential của nguồn nó phục vụ.

### Negative
- 9 bộ biến môi trường → `claude_desktop_config.json` dài; giảm bằng `config-emit`.

### Risks
- Người dùng vẫn có thể để token quá rộng trong env. Giảm thiểu: lệnh `doctor` cảnh báo khi
  credential có quyền ghi (ADR-0003 lớp 3).
