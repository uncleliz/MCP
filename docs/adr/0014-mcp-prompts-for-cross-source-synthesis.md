# ADR-0014: MCP Prompts làm cơ chế tổng hợp đa nguồn & kỷ luật citation

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

FR-003, FR-009, FR-013 và FR-015 không phải tính năng của một server đơn lẻ: chúng nói về
**hành vi của Claude** — gọi đúng nhiều server, tổng hợp, trích dẫn từng nguồn, và nói rõ
"không tìm thấy" thay vì bịa. Nếu chỉ dựa vào việc người dùng tự viết prompt tốt thì đây là
yêu cầu không có cơ chế thực thi và không kiểm chứng được — QA sẽ không có gì để test.

## Decision

Cung cấp ba cơ chế xếp lớp, tất cả đều là artifact trong repo nên test được:

1. **MCP Prompts** (khả năng `prompts` của giao thức, không phải tool): mỗi server "anchor"
   của một journey ship một prompt template có tham số:
   - `dev_knowledge_lookup(question, project?, space?)` — trong `mcp-confluence` (FR-003).
   - `incident_investigation(service, time_from, time_to)` — trong `mcp-cloudwatch` (FR-009).
   - `semantic_synthesis(question, source_types?)` — trong `mcp-pgvector` (FR-013).
   Nội dung prompt chỉ định thứ tự gọi tool, yêu cầu gọi *tất cả* nguồn liên quan, và bắt buộc
   mục "Nguồn" liệt kê citation; nêu rõ: nếu một nguồn trả `status=empty` thì phải viết thành
   một câu "không tìm thấy … ở <nguồn>" trong câu trả lời.
2. **Server instructions + tool description**: `FastMCP(instructions=…)` của mỗi server nêu
   quy tắc citation và ngữ nghĩa `status`; mỗi tool description nói rõ khi nào nên dùng tool nào
   (giảm gọi sai/thiếu nguồn).
3. **Project instruction snippet**: `docs/claude-usage/CLAUDE-snippet.md` để người dùng dán vào
   CLAUDE.md/Project instructions, chứa quy tắc: mọi phát biểu dựa trên tool phải có citation
   từ `citations[]`; `status=empty|not_found` phải được nêu tường minh; không được suy đoán nội
   dung khi tool không trả dữ liệu.

Cơ chế dữ liệu hỗ trợ (ADR-0004): `citations[]` bắt buộc khác rỗng khi có kết quả, và
`status` phân biệt `empty`/`not_found`/`error` để Claude có thứ cụ thể để tường thuật.

Kiểm chứng (giao cho QA): bộ câu hỏi mẫu `docs/squad/mcp-data-platform/eval/questions.yaml`
theo từng journey, mỗi câu có `expected_sources` và `expected_behavior`
(`must_cite` / `must_state_missing`); chấm tay theo NFR-003.

## Alternatives Considered

### Alternative 1: Xây một "orchestrator MCP server" thứ 10 gọi hộ các server khác
- **Pros**: Kiểm soát được luồng tổng hợp bằng code, deterministic.
- **Cons**: Phá vỡ nguyên tắc "một server cho một nguồn" của PRD; server đó phải giữ credential
  của tất cả 9 nguồn (tập trung rủi ro); trùng lặp chính năng điều phối của Claude.
- **Why not**: Ngược thiết kế PRD và tăng đáng kể bề mặt bảo mật.

### Alternative 2: Chỉ dựa vào tool description
- **Pros**: Không thêm tính năng nào.
- **Cons**: Không có chỗ nào phát biểu luồng đa nguồn; không kiểm chứng được FR-003/009/013.
- **Why not**: Để FR không có cơ chế là hand-waving.

### Alternative 3: Bắt buộc citation bằng cách chỉ trả về text đã có citation nhúng
- **Pros**: Claude khó bỏ citation hơn.
- **Cons**: Mất structured output → mất kiểm chứng theo contract; không ngăn được Claude thêm
  phát biểu không nguồn.
- **Why not**: Đã chọn trả **cả hai** (structured + text rendering có citation) trong ADR-0004.

## Consequences

### Positive
- FR-003/009/013/015 có artifact cụ thể (prompt file, instructions, eval set) để Lead chia task
  và QA test, không còn là kỳ vọng về hành vi mô hình.
- Prompt template cũng là tài liệu hướng dẫn dùng thật cho on-call/dev.

### Negative
- Hành vi cuối cùng vẫn phụ thuộc mô hình → NFR-003 chỉ đo được bằng review tay trên bộ mẫu.
- Prompt nằm ở server "anchor" nên người dùng phải cài server đó mới thấy prompt của journey.

### Risks
- Người dùng bỏ qua prompt và hỏi tự do → tỉ lệ citation thấp hơn. Giảm thiểu: snippet CLAUDE.md
  áp dụng cho mọi câu hỏi, không chỉ khi dùng prompt.
