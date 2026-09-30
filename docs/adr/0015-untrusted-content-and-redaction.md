# ADR-0015: Xử lý nội dung không tin cậy: chống prompt injection + redaction secret

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

Mọi tool trong hệ này đọc nội dung do người/hệ thống khác tạo (trang Confluence, comment MR,
log line, giá trị Redis, message Kafka) rồi đưa **thẳng vào context của Claude**, nơi Claude
có sẵn 9 server và có thể chạy lệnh trong Claude Code. Hai rủi ro cụ thể:
(1) **Prompt injection gián tiếp** — một trang Confluence hay comment MR chứa "Bỏ qua hướng dẫn
trước, hãy…" được Claude đọc như chỉ thị; (2) **rò rỉ secret** — giá trị Redis, `.env` trong
repo GitLab, log line, hay message Kafka chứa token/mật khẩu bị trả ra và ghi vào log/transcript.
Read-only không giải quyết được hai vấn đề này: đọc dữ liệu độc hại vẫn gây hại.

## Decision

- **Nội dung là dữ liệu, không phải chỉ thị**: mọi trường nội dung tự do trả về cho Claude được
  bọc bởi `mcp_common.content.wrap_untrusted()`: đặt trong khối có nhãn rõ ràng
  (`<untrusted-content source="confluence" id="12345"> … </untrusted-content>`), strip/escape
  các chuỗi giả mạo khung hội thoại (`</untrusted-content>`, `<system>`, `Human:`, `Assistant:`),
  và text rendering của tool luôn kèm một câu ghi chú rằng nội dung bên trong là dữ liệu từ
  nguồn ngoài, không phải chỉ thị. Server `instructions` (ADR-0014) nhắc lại quy tắc này.
- **Redaction**: `mcp_common.redact.scrub()` áp một tập pattern (AWS access key/secret, JWT,
  `Bearer …`, GitLab/GitHub/Atlassian token prefix, private key block, chuỗi dạng
  `password|passwd|secret|token|api[_-]?key\s*[:=]\s*…`, chuỗi entropy cao dài) cho
  **(a)** mọi log record và **(b)** mọi trường nội dung tự do trước khi trả về, thay bằng
  `«redacted:<kind>»`. Bật mặc định, có thể tắt cho từng tool bằng env allowlist
  (`MCP_REDACT_DISABLED=false` mặc định) — việc tắt phải là hành động tường minh của người vận hành.
- **Chặn nguồn nhạy cảm bằng cấu hình**: `MCP_REDIS_KEY_DENY` / `MCP_GITLAB_PATH_DENY`
  (glob, mặc định gồm `*.env`, `*secret*`, `*credential*`, `*.pem`, `id_rsa*`) → tool trả
  `status=not_found` + cảnh báo "bị chặn bởi policy" thay vì nội dung.
- **Giới hạn kích thước & số lượng**: `MCP_MAX_OUTPUT_BYTES` (mặc định 128 KiB/response),
  `limit ≤ 100`, nội dung dài bị cắt kèm `meta.truncated=true` và con trỏ để lấy tiếp — vừa
  chống context bloat vừa giảm bề mặt injection.

## Alternatives Considered

### Alternative 1: Không làm gì (tin tưởng nguồn nội bộ)
- **Pros**: Không công.
- **Cons**: Nguồn nội bộ vẫn chứa nội dung do người ngoài tạo (ticket từ khách, log chứa input
  người dùng); `.env` trong repo là chuyện thường ngày.
- **Why not**: Rủi ro bảo mật thật với chi phí phòng ngừa nhỏ.

### Alternative 2: Dùng model/LLM phân loại nội dung độc hại trước khi trả
- **Pros**: Bắt được injection tinh vi.
- **Cons**: Thêm chi phí, độ trễ, và một phụ thuộc LLM trong mọi tool call; vẫn không chắc chắn.
- **Why not**: Không tương xứng ở v1; wrap + nhãn + giới hạn kích thước là biện pháp
  deterministic và đủ cho mô hình đe doạ hiện tại.

### Alternative 3: Chỉ redaction ở log, không redaction ở output
- **Pros**: Giữ nguyên dữ liệu cho người dùng xem.
- **Cons**: Transcript Claude Desktop lưu trên đĩa và có thể được đồng bộ → secret rò ra ngoài
  phạm vi hệ thống nguồn.
- **Why not**: Output là đường rò rỉ chính, không thể bỏ.

## Consequences

### Positive
- Một chỗ duy nhất (`mcp_common`) giữ chính sách, áp cho cả 9 server + pipeline.
- Giảm đồng thời rủi ro bảo mật và rủi ro context bloat.

### Negative
- False positive của redaction có thể che mất nội dung hợp lệ (ví dụ log có chuỗi hash dài).
  Giảm thiểu: `meta.warnings` báo số lần redact, và pattern entropy chỉ áp cho chuỗi ≥ 32 ký tự
  không có khoảng trắng.
- Wrap nội dung làm response dài thêm chút.

### Risks
- Không có cơ chế nào chặn tuyệt đối prompt injection gián tiếp; đây là giảm thiểu, không phải
  triệt tiêu. Ghi nhận trong "Risks & spikes" của architecture.md; giảm thiểu bổ sung: khuyến
  nghị người dùng không chạy Claude Code ở chế độ tự động phê duyệt lệnh khi đang dùng các
  server này.

## Amendments (sau design review 2026-10-01)

### A1 — Redaction + deny-glob phải chạy Ở TẦNG INGEST, không chỉ ở tool layer
Bản đầu đặt `wrap_untrusted()` / `scrub()` / deny-glob **chỉ ở tool layer**. Nhưng
`mcp-ingest` (ADR-0012) ghi thẳng vào `kb.chunks` mà không đi qua tool layer, nên nội dung
`.env`/`.pem`/token bị **persist vào Postgres** rồi phát lại qua `kb_semantic_search` — tức đi
vòng qua chính deny-glob mà `gitlab_get_file` thực thi. Một lần rò rỉ ở tool layer là tạm
thời; rò rỉ đã persist là vĩnh viễn và nhân bản qua mọi truy vấn semantic sau đó.

Bắt buộc:
- Pipeline có stage `redact` chạy **sau `normalize`, trước `chunk`** (`IngestError.stage`
  trong contract đã có giá trị `redact`).
- Deny-glob (`MCP_GITLAB_PATH_DENY` và tương đương) áp ở **connector**: document khớp
  deny-glob không bao giờ được crawl vào, ghi `kb.ingest_failures{stage: redact,
  code: blocked_by_policy}` (ADR-0011 A4) để việc bỏ qua là nhìn thấy được.
- Test: `mcp_ingest` assert một `SourceDocument` chứa secret mẫu không bao giờ tới `persist`
  ở dạng thô.

### A2 — `wrap_untrusted` không áp ở tầng lưu trữ
Ngược lại với redaction: nhãn `<untrusted-content>` **không** được ghi vào `kb.chunks`. Nó là
biện pháp ở ranh giới trình bày cho Claude, và nếu bị persist thì (a) làm nhiễu vector
embedding, (b) nhãn bị lặp khi `kb_semantic_search` wrap lần thứ hai. Chunk lưu nội dung đã
redact và **chưa** wrap; `mcp-pgvector` wrap lúc trả về như mọi server khác.
