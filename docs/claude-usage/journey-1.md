# Journey 1 — Dev tra cứu tài liệu + code (Phase 1, T-029)

Phục vụ FR-003 (AC-001: có citation từ cả Confluence và GitLab; AC-002: nguồn rỗng thì phải nói
rõ), FR-015 và NFR-003 (đo tỉ lệ câu trả lời có citation hợp lệ bằng review tay).

## 1. Chuẩn bị

1. Tạo tài khoản Confluence Cloud **chỉ-xem** (viewer) và PAT GitLab chỉ có `read_api` và/hoặc
   `read_repository` (server từ chối khởi động nếu token ghi được, ADR-0003 A1).
2. Điền biến môi trường theo `.env.example` (`MCP_CONFLUENCE_*`, `MCP_GITLAB_*`).
3. Sinh cấu hình Claude: `uv run mcp-common config-emit --server confluence` và `--server gitlab`,
   dán vào `claude_desktop_config.json` (xem `docs/claude-usage/README.md`).
4. Chẩn đoán: `uv run mcp-confluence doctor` và `uv run mcp-gitlab doctor` phải báo `ok`.

## 2. Bộ câu hỏi

`eval/questions.yaml` có 12 câu (8 câu hai nguồn, 1 câu chỉ GitLab, 3 câu có nguồn rỗng, 1 câu
về id không tồn tại). Từ khoá `confluence_query` / `gitlab_query` là **mẫu, chưa kiểm chứng trên
tenant thật**: thay bằng từ khoá khớp nội dung thật trước khi chạy, giữ nguyên
`expected_sources` / `expect_empty`.

## 3. Chạy

```bash
uv run python scripts/run_eval.py --dry-run     # chỉ kiểm tra file câu hỏi, không cần mạng
uv run python scripts/run_eval.py               # chạy thật, ghi eval/results/<thời điểm>.md
uv run python scripts/run_eval.py --only Q-J1-009
```

Script gọi đúng read API mà tool MCP dùng (chỉ GET) và ghi bảng: với mỗi câu, trạng thái và số
citation của Confluence/GitLab, link citation, và kết luận ở mức tool (`PASS`/`FAIL` theo
`expected_sources` và `expect_empty`). Exit code 1 nếu có câu không đạt kỳ vọng ở mức tool.

## 4. Review tay (phần NFR-003 thực sự đo)

Script chỉ kiểm **tool trả gì**. Claude có tuân thủ kỷ luật citation hay không phải xem bằng tay:

1. Bật hai server `confluence` và `gitlab` trong Claude Desktop/Code (không bật thêm server khác).
2. Với mỗi câu trong `eval/questions.yaml`, chạy prompt `dev_knowledge_lookup` với `question` là
   câu đó (hoặc dán câu hỏi thẳng và nhắc dùng cả hai nguồn).
3. Điền vào bảng kết quả hai cột:
   - **Claude trả lời có citation?** đánh dấu nếu mục `Nguồn` có đủ URL của mọi nguồn đã dùng và
     mỗi URL mở được.
   - **Nêu rõ nguồn rỗng?** (chỉ với câu `expect_empty`) đánh dấu nếu câu trả lời nói rõ kiểu
     "không tìm thấy tài liệu Confluence cho ..." và không bịa citation.
4. Tỉ lệ = số câu đạt / tổng số câu. Ngưỡng do PO chốt sau khi Phase 1 chạy (NFR-003).

## 5. Hành vi đúng cho case nguồn rỗng (FR-003/AC-002)

Khi `confluence_search_pages` trả `status=empty`, văn bản tool trả về bắt đầu bằng
`Không tìm thấy tài liệu Confluence cho "<từ khoá>" ở Confluence.`, không có mục `Nguồn`, và
`structuredContent` có `items: []`, `citations: []`, `meta.query_echo` ghi phạm vi đã tra. Prompt
`dev_knowledge_lookup` yêu cầu Claude nêu rõ nguồn không có dữ liệu thay vì bỏ qua hoặc bịa.
