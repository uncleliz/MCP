# Sign-off Phase 1 — Confluence + GitLab (T-031)

Trạng thái: **phần tự động xong và xanh; phần thủ công cần tài khoản thật/VPN CHƯA làm được
trong container của squad** (không có credential Confluence Cloud/GitLab, không có VPN, không có
Claude Desktop). Các mục thủ công để trống có chủ đích, không được đánh dấu giả.

## A. Đã kiểm chứng tự động (`make ci` xanh)

| Mục | Bằng chứng |
|---|---|
| 15 tool (4 Confluence + 11 GitLab) trả lời `tools/list` | `packages/mcp_confluence/tests/test_phase1_surface.py`; `test_settings_cli.py` của mỗi package chạy `serve` qua stdio thật và nhận `tools/list` |
| 0 tool ghi; mọi tool là operation `x-readonly: true`, `x-side-effects: none` | `test_tools_readonly.py` của cả hai package (`assert_readonly_tool_surface`) |
| Gọi tool ghi không tồn tại bị SDK từ chối ở tầng JSON-RPC (FR-014 AC-002) | `test_FR_014_AC_002_*` (5 tên ở Confluence, 8 tên ở GitLab) |
| Mọi request đi ra là GET/HEAD; POST/PUT/DELETE/PATCH bị transport chặn | `readonly_respx_router` + `test_transport_blocks_every_write_method`, `test_NFR_001_typical_write_operations_blocked_by_transport` |
| `body.export_view` bị loại ở tầng code (ADR-0007 A1) | `test_ADR_0007_A1_export_view_rejected_in_code` |
| PAT GitLab có scope ngoài `read_api`/`read_repository` thì từ chối serve | `test_FR_002_AC_003_unsafe_token_fails_startup_check`, `test_api_scope_token_refuses_to_serve`, `test_serve_refuses_token_with_api_scope` |
| Tài khoản Confluence ghi được thì từ chối serve (fail-closed) | `test_FR_001_AC_003_account_that_can_write_fails_startup_check`, `test_serve_refuses_when_credential_check_fails` (Confluence) |
| Deny-glob `.env`/`*.pem`/... chặn `gitlab_get_file`, search, diff MR | `test_FR_002_AC_003_*` |
| Snapshot khớp contract, 4 nhánh `ok/empty/not_found/error` validate theo `api-contract.yaml` | `test_contract.py` của cả hai package |
| NFR-002: 2 lần thử, 21s ảo, < 25s, `hint` nhắc VPN, deadline riêng theo tool | `test_timeout_budget.py` của cả hai package |
| Bộ câu hỏi eval hợp lệ (12 câu, có case nguồn rỗng) | `test_eval_script.py`; `uv run python scripts/run_eval.py --dry-run` |

Kết quả gần nhất: 522 test pass, 2 skip (`@pytest.mark.live`, lý do: cần credential/VPN);
coverage `mcp_confluence` 97%, `mcp_gitlab` 95%, `mcp_common` 94%; ruff, mypy, validate contract
và suite read-only (62 test) xanh.

## B. Checklist thủ công (người có VPN + tài khoản thật điền)

- [ ] Tài khoản Confluence Cloud là **viewer-only**; `uv run mcp-confluence doctor` báo `ok`.
      Lưu ý: cách chứng minh "không ghi được" (đọc `operations` của một page mẫu) dựa trên hành vi
      API Cloud theo tài liệu, **chưa kiểm chứng trên tenant thật**; nếu Atlassian trả dạng khác,
      `doctor` sẽ fail-closed và in lý do. Ghi lại kết quả thật ở đây: ______
- [ ] PAT GitLab chỉ có `read_api`/`read_repository`; `uv run mcp-gitlab doctor` báo `ok`.
      Kết quả: ______
- [ ] `doctor` không chạy được vì VPN: ghi lý do và xem `docs/spikes/S1-reachability.md`.
- [ ] Dán JSON từ `uv run mcp-common config-emit --server confluence` và `--server gitlab` vào
      `claude_desktop_config.json`; khởi động lại Claude Desktop/Code.
- [ ] Hai server hiện trong danh sách MCP của Claude, tổng 15 tool (4 + 11), 0 tool ghi (NFR-005).
- [ ] Hỏi một câu Journey 1 (ví dụ `Q-J1-001`): câu trả lời có ít nhất một URL Confluence và một
      URL GitLab trong mục `Nguồn`.
- [ ] Hỏi câu `Q-J1-009`: câu trả lời nêu rõ không tìm thấy tài liệu Confluence, không bịa citation.
- [ ] Chạy `uv run python scripts/run_eval.py` (sau khi thay từ khoá mẫu bằng từ khoá thật) và lưu
      `eval/results/*.md`; hoàn tất review tay theo `docs/claude-usage/journey-1.md` (NFR-003).
- [ ] Chạy lại `make ci` trên máy dev: xanh.

Người ký: ______  Ngày: ______
