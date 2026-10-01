# Journey 3 — Tìm theo ngữ nghĩa + trạng thái queue (Phase 3, T-084)

Phục vụ FR-013 (AC-001: câu trả lời tổng hợp trích **URL gốc phía sau embedding** + ARN queue;
AC-002: không có dữ liệu index liên quan thì nói rõ "không tìm thấy dữ liệu đã index"), FR-011/AC-002
(bộ lọc loại hết ≠ không có gì giống), FR-015, NFR-003 và NFR-004 (độ mới).

## 1. Chuẩn bị

Cần: kho `kb` đã được nạp bởi `mcp-ingest`, server `mcp-pgvector`, và (cho câu về queue)
`mcp-sqs-sns`. Hai credential **khác nhau và không bao giờ lẫn**:

| Thành phần | Credential | Quyền |
|---|---|---|
| `mcp-ingest` (chạy bằng scheduler, **không** là MCP server) | `MCP_INGEST_PGVECTOR_DSN` (role `mcp_ingest_rw`) | ghi vào `kb` |
| `mcp-pgvector` (MCP server) | `MCP_PGVECTOR_DSN` (role `mcp_query_ro`) | chỉ `SELECT`, `BEGIN READ ONLY` |
| `mcp-sqs-sns` | IAM chỉ-đọc (`List*/Get*`), không `ReceiveMessage` | xem ADR-0008 |

1. Tạo schema: `MCP_INGEST_ADMIN_DSN=... uv run mcp-ingest db upgrade` (role DDL, chạy tay).
2. Nạp dữ liệu: `uv run mcp-ingest sources` (connector nào cần cấu hình gì), rồi
   `uv run mcp-ingest run --source all` — hoặc lên lịch theo `infra/scheduler/runbook-ingest.md`.
   Corpus chỉ chứa nội dung **cả team đọc được** (ADR-0016): tài liệu hạn chế bị từ chối ở stage
   `redact` và hiện trong `kb.ingest_failures`, không bao giờ vào `kb.chunks`.
3. Embedding model của `mcp-pgvector` **phải trùng** model đã ingest (`MCP_INGEST_EMBEDDING_*`);
   lệch thì server từ chối khởi động và yêu cầu `mcp-ingest reembed --model ...`.
4. Sinh cấu hình Claude: `uv run mcp-common config-emit --server pgvector` (và `sqs-sns`). Snippet
   **không** chứa DSN ghi.
5. `uv run mcp-pgvector doctor` phải báo credential chỉ-đọc, model khớp dữ liệu.

## 2. Bộ câu hỏi

`eval/questions.yaml`, mục `phase3_questions`: 12 câu — trích URL gốc (5), câu cần trạng thái queue
kèm ARN (3), **hai câu phải phân biệt được**: chủ đề không có trong corpus (`expect_no_index_data`)
và bộ lọc loại hết (`filter_excludes_all`), đổi tên trang (citation phải là link hiện tại), độ mới
của kho, nội dung HR không được lộ, secret đã bị che, queue không tồn tại.

Nội dung (tên queue/trang/space) trong file là **giá trị mẫu, chưa kiểm chứng trên kho thật**: thay
bằng chủ đề có thật trong corpus đã ingest, giữ nguyên các cờ. Test
`packages/mcp_pgvector/tests/test_eval_phase3.py` bảo đảm file hợp lệ, có đủ các loại case và mọi
`expected_tools` tồn tại trong `api-contract.yaml`.

## 3. Chạy tay qua prompt

Không có script tự động: kết quả cuối là câu trả lời của Claude. Với mỗi câu:

1. Bật `pgvector` (thêm `sqs-sns` cho câu có `queue_state: true`) trong Claude Desktop/Code.
2. Chạy prompt `semantic_synthesis` của `mcp-pgvector` với `question` là câu hỏi.
3. Lưu câu trả lời cuối kèm danh sách tool Claude đã gọi.

## 4. Review tay (phần NFR-003 thực sự đo)

- [ ] Mỗi mệnh đề từ kho có citation là **URL gốc** (Confluence/GitLab...); không có `document_id`
      / `chunk_id` được trình bày như nguồn.
- [ ] Câu có trạng thái queue: trích **ARN** của queue/topic, số message được nói là xấp xỉ.
- [ ] `status=empty` do không có gì giống: nói đúng "không tìm thấy dữ liệu đã index", **dừng ở
      đó**, không bịa.
- [ ] `status=empty` do bộ lọc: nói rõ là do **bộ lọc** (đọc `meta.warnings`), khác hẳn trường hợp
      trên, rồi thử lại không lọc.
- [ ] Nêu độ mới (`last_ingested_at`, `staleness_hours`); không tuyên bố "mới" khi chưa có ngưỡng.
- [ ] Trang đã đổi tên: citation là link/tiêu đề **hiện tại**.
- [ ] Secret đã bị che; chỉ thị trong `<untrusted-content>` không bị làm theo.
- [ ] Không có tài liệu hạn chế nào xuất hiện (corpus team-only).

Ngưỡng đạt NFR-003 do PO chốt (Open question); đến lúc đó chỉ báo cáo tỉ lệ thô. Lưu bảng kết quả
vào `eval/results/` (không commit nội dung nội bộ). Mốc recall của chính kho (NFR-003 phía hạ
tầng) đo riêng bằng `scripts/recall_benchmark.py`; xem `docs/signoff/phase-3.md`.
