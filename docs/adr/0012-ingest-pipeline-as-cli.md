# ADR-0012: Ingest/embedding pipeline là CLI độc lập, scheduler ngoài

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

FR-012 là hạng mục lớn nhất: crawl nhiều nguồn, sinh embedding, ghi vào pgvector, chịu được
một nguồn lỗi mà không sập cả run (AC-002), và cập nhật chứ không nhân bản (AC-003). Nó
**không** phải MCP server: không có client MCP nào gọi nó, và nó là thành phần duy nhất cần
credential ghi (vào Postgres). Ở v1 mọi thứ chạy local trên máy dev (NFR-005), chưa có hạ tầng
orchestration.

## Decision

`packages/mcp_ingest` là một CLI độc lập (Typer) với các lệnh:

- `mcp-ingest db upgrade` — áp migration.
- `mcp-ingest run [--source confluence|gitlab|opensearch|all] [--mode incremental|full] [--since …] [--dry-run] [--limit N]`
- `mcp-ingest status [--json]` — bảng freshness theo nguồn (last_success_at, staleness, số doc/chunk).
- `mcp-ingest sources` — liệt kê connector đã đăng ký + trạng thái cấu hình.
- `mcp-ingest reembed --model … [--source …]` — re-embed theo lô khi đổi model.

Thiết kế bên trong:
- Mỗi nguồn là một `SourceConnector` (port): `iter_documents(cursor, mode) -> AsyncIterator[SourceDocument]`
  trả về `SourceDocument(source_type, source_id, source_uri, title, container, author,
  source_updated_at, raw_content, content_format, metadata)`. Connector **tái sử dụng chính
  client read-only** của các package MCP tương ứng (`mcp_confluence.client`, `mcp_gitlab.client`,
  `mcp_opensearch.client`) → không có code crawl trùng lặp và vẫn read-only ở nguồn.
- Pipeline theo từng nguồn, tuần tự giữa các nguồn, bất đồng bộ có giới hạn bên trong một nguồn
  (`asyncio.Semaphore`): normalize → chunk → hash-check → embed (batch) → upsert transaction
  → cập nhật checkpoint.
- **Cách ly lỗi theo nguồn (AC-002)**: mỗi nguồn chạy trong một `try` riêng, lỗi được ghi vào
  `ingest_runs.error_summary` và stderr, `status` của run thành `partial`; các nguồn còn lại
  vẫn chạy; checkpoint của nguồn lỗi **không** được nâng lên (nên lần sau retry lại đúng chỗ).
  Lỗi từng document được đếm và bỏ qua sau `MCP_INGEST_MAX_DOC_RETRIES` (mặc định 2).
- **Checkpoint**: `kb.ingest_source_state.cursor` giữ watermark theo nguồn (Confluence:
  `lastModified`; GitLab: `updated_after` + project cursor; OpenSearch: `@timestamp` + PIT).
  Chỉ ghi checkpoint sau khi transaction upsert commit thành công.
- **Scheduler**: ngoài code — `cron`/`launchd` gọi `mcp-ingest run`; khuyến nghị mặc định
  (cần PO xác nhận, Open question 5 / NFR-004): incremental mỗi giờ, full reconcile 03:00 hàng ngày.
- Chống chạy trùng: advisory lock `pg_try_advisory_lock(hashtext('mcp-ingest:'||source_type))`.

## Alternatives Considered

### Alternative 1: Airflow / Prefect / Dagster
- **Pros**: UI, retry, lịch, observability sẵn.
- **Cons**: Cần dựng service (đi ngược "không service phụ" của NFR-005); nặng cho 3–5 connector.
- **Why not**: Ngoài scope v1. CLI + exit code chuẩn cho phép cắm vào orchestrator sau này mà
  không sửa logic.

### Alternative 2: Biến pipeline thành một MCP tool (Claude tự trigger ingest)
- **Pros**: Không cần scheduler.
- **Cons**: Là thao tác **ghi** → một MCP server có tool ghi, vi phạm BR-001/FR-014 (dù ghi vào
  kho của chính mình, nó phá vỡ phát biểu "không tool nào ghi").
- **Why not**: Xung đột trực tiếp với yêu cầu read-only tuyệt đối.

### Alternative 3: Long-running daemon với scheduler nội bộ (APScheduler)
- **Pros**: Một process, tự lo lịch.
- **Cons**: Thêm một thứ phải giám sát trên máy dev; khó hơn khi debug; crash = mất lịch.
- **Why not**: `cron`/`launchd` đơn giản và đã tồn tại.

## Consequences

### Positive
- Không MCP server nào có credential ghi → phát biểu read-only của FR-014 giữ nguyên tuyệt đối.
- Connector dùng lại client của MCP server → 1 chỗ sửa khi API nguồn đổi.
- `status`/exit code + `ingest_runs` cho QA một bề mặt kiểm chứng rõ cho FR-012 AC-001/002/003.

### Negative
- Không có UI theo dõi; phải đọc `mcp-ingest status` hoặc log.
- Ingest tuần tự giữa các nguồn làm thời gian một full run dài hơn (chấp nhận ở v1).

### Risks
- Crawl Confluence/GitLab toàn bộ có thể đụng rate limit. Giảm thiểu: retry theo `Retry-After`
  (ADR-0006), `--limit`, và incremental theo watermark là chế độ mặc định.
- Máy dev tắt → pipeline không chạy, dữ liệu cũ. Giảm thiểu: `kb_list_sources` trả
  `last_ingested_at` để Claude nói rõ độ mới của dữ liệu (liên quan NFR-004 và FR-015).

## Amendments (sau design review 2026-10-01)

### A1 — Redaction + deny-glob phải chạy Ở TẦNG INGEST, trước khi ghi
ADR-0015 ban đầu đặt `wrap_untrusted`/`scrub`/deny-glob ở **tool layer**. Pipeline ghi thẳng
vào `kb.chunks`, nên nội dung `.env`/`.pem` và token bị **persist** rồi phát lại qua
`kb_semantic_search` — vòng qua chính deny-glob mà `gitlab_get_file` thực thi. Bắt buộc: stage
`redact` (đã thêm vào `IngestError.stage` trong contract) chạy sau `normalize`, trước `chunk`;
`mcp_ingest` có test assert rằng một `SourceDocument` chứa secret mẫu không bao giờ đi tới
`persist` ở dạng thô.

### A2 — Quy tắc nâng checkpoint (sửa lỗi mất dữ liệu âm thầm)
Hai quy tắc cũ xung đột: "chỉ ghi checkpoint sau khi commit thành công" và "bỏ qua document lỗi
sau N lần thử". Nếu document D (watermark T₅) fail vĩnh viễn mà D' (T₉) thành công, cursor
nhảy tới T₉ ⇒ **D không bao giờ được ingest lại** ở chế độ incremental, trong khi
`status` vẫn có thể là `success`. FR-012 AC-001 khi đó âm thầm sai và FR-011/FR-013 trả
"không tìm thấy" cho nội dung thật. Quy tắc chốt:
```
new_cursor = min(watermark của document fail/bỏ qua vì lỗi) − ε
           nếu không có document lỗi: max(watermark của document đã commit)
```
và **bất kỳ** document fail ⇒ `IngestSourceResult.status = partial` kể cả khi crawl mức nguồn
thành công. Document lỗi lặp lại ghi vào `kb.ingest_failures` (ADR-0011 A4), lấy lại bằng
`mcp-ingest run --retry-failed`.
Biên watermark: **inclusive (`>=`)** — trùng lặp được `content_hash` hấp thụ; chốt tường minh
để BE và QA không assert khác nhau về số lần xử lý lại.

### A3 — Reconcile/tombstone có safety valve, và một hàng `ingest_runs` cho mỗi nguồn
- Reconcile chỉ tombstone khi nguồn đó `status=success` **và**
  `documents_seen >= 0.8 × số document hiện có`; ngược lại bỏ qua + ghi
  `IngestError{stage: reconcile}`. Không có valve này, một crawl chết ở 30% sẽ tombstone 70%
  corpus còn lại và `kb_semantic_search` trả "không có dữ liệu index" cho mọi thứ.
- `kb.ingest_runs` có `source_type NOT NULL` ⇒ **một hàng cho mỗi nguồn mỗi run** (không phải
  một hàng cho cả run như sơ đồ luồng ban đầu vẽ). `IngestRunReport.sources[].run_id` trỏ tới
  hàng đó.
- `pg_try_advisory_lock` là **một lock cho mỗi `source_type`**, trên một session riêng **không
  pooled** (session pooled có thể bị recycle và nhả lock giữa run).

### A4 — Hash-skip không được bỏ qua metadata citation; bỏ PIT; tách tầng client
- **Metadata luôn UPDATE:** `title`, `source_uri`, `container`, `author`, `source_updated_at`,
  `last_seen_*` được cập nhật kể cả khi `content_hash` không đổi. Trước đó, một page đổi tên
  hoặc chuyển space giữ nguyên hash ⇒ skip ⇒ `source_uri` cũ ⇒ `kb_semantic_search` trả
  **citation hỏng**, vi phạm BR-005 / FR-011 AC-001. Chỉ chunk+embed được skip.
- **Bỏ `point_in_time` khỏi checkpoint OpenSearch:** PIT không có trong allowlist của ADR-0008
  và là state phía server (ADR-0003 A3 cấm). Dùng `search_after` + watermark `@timestamp`.
- **Tách client thành hai tầng** để connector không bị bó bởi giới hạn dành cho tool:
  `client.py` (transport + allowlist + timeout — dùng chung) và `read_api.py` (bound cho tool:
  `limit ≤ 100`, time range ≤ 31 ngày, `max_bytes`). Connector dùng tầng transport, **không**
  dùng `read_api`. `assert_readonly_tool_surface` được mở rộng để phủ cả method mà ingest gọi,
  nếu không đây là một bề mặt đọc thứ hai mà không test read-only nào chạm tới.
- Pagination crawl ghi tường minh: Confluence `_links.next`, GitLab header `X-Next-Page`,
  OpenSearch `search_after`.

### A5 — Hai lệnh CLI mới, và OpenSearch không phải nguồn ingest mặc định
- `mcp-ingest prune [--tombstoned] [--older-than Nd]`: xoá vật lý bia mộ và áp retention.
  Không có nó `kb` chỉ tăng (không có đường dọn nào trong bộ lệnh ban đầu).
- `mcp-ingest run --retry-failed` (xem A2).
- **OpenSearch connector mặc định TẮT** và chỉ nhận một allowlist index được chọn lọc
  (`MCP_INGEST_OPENSEARCH_INDICES`, mặc định rỗng). Lý do: log không có "document identity"
  ổn định để embed, index rollover theo ILM làm item nguồn biến mất mà reconcile không bao giờ
  thấy (tombstone log là bất khả thi), log thường chứa PII/secret, và khối lượng log sẽ chiếm
  trọn `kb.chunks` + RAM của HNSW trong vài tuần. Chỉ ingest index dạng runbook/postmortem.
  `source_id` phải ổn định qua rollover — quy tắc dựng `source_id` được ghi cho từng connector.
