# ADR-0016: Cột `visibility` và đường mở sang RBAC per-user khi chuyển remote

**Date**: 2026-10-01
**Status**: proposed — **cần PO/user quyết** (Open question 3); không implement ở v1
**Deciders**: SA (squad-sa) đề xuất; PO + SA quyết theo BR-003

## Context

BR-003 giả định **truy cập đồng nhất**: mọi thành viên có cùng quyền đọc trên cả 9 nguồn, và
v1 không implement per-user filtering. Giả định này an toàn ở v1 **vì một lý do cụ thể**: mỗi
người chạy MCP server bằng **credential của chính mình** trên máy của mình, nên phân quyền
được thừa hưởng nguyên vẹn từ hệ nguồn (Confluence/GitLab/AWS đã có phân quyền riêng). Không
có code RBAC nào cần viết.

Nhưng có **một ngoại lệ đã tồn tại ngay ở v1**: `kb` (Postgres + pgvector) là nguồn duy nhất
mà dữ liệu bị **sao chép ra khỏi hệ nguồn**. Pipeline crawl bằng credential của nó
(`mcp_ingest_rw`) rồi `mcp-pgvector` đọc bằng `mcp_query_ro` — hai credential **không liên
quan gì** tới người đang hỏi. Nghĩa là: một trang Confluence trong space bị giới hạn, một khi
đã embed, sẽ trả về cho **bất kỳ ai** chạy `kb_semantic_search`. Đây chính là R13 nhưng nó
không chờ tới lúc mở HTTP mới xuất hiện — nó xuất hiện ngay khi Phase 3 chạy, nếu corpus có
nội dung không đồng nhất quyền.

## Decision (đề xuất)

Hai phần, tách rõ cái gì làm bây giờ và cái gì chờ quyết định:

**Phần 1 — làm ở Phase 3 (đã chốt trong ADR-0011 A1):** `kb.documents` có cột
`visibility text NOT NULL DEFAULT 'team'`. Connector gán giá trị khi crawl (ví dụ space
Confluence public → `team`; space hạn chế / project GitLab private → `restricted`). Cột này
**chưa được dùng để filter** ở v1; nó tồn tại để (a) việc mở RBAC sau này không phải re-ingest
toàn bộ corpus, (b) có thể trả lời được câu "corpus có chứa nội dung hạn chế không" bằng một
câu SQL thay vì phải audit tay.

**Phần 2 — chờ PO quyết (Open question 3):** nếu PO xác nhận corpus **chỉ** gồm nội dung
đồng nhất quyền, `mcp-ingest` phải **từ chối** ingest document có `visibility != 'team'` và
báo qua `kb.ingest_failures` — an toàn theo mặc định, và biến giả định BR-003 thành một ràng
buộc kiểm chứng được thay vì một lời hứa. Nếu PO muốn ingest cả nội dung hạn chế thì RBAC
per-user trở thành **must-have của Phase 3**, không phải việc tương lai, và cần thêm: identity
per-request, mapping identity → nhóm quyền, và filter `visibility` trong mọi truy vấn FR-011.

Khi mở HTTP+SSE (BR-004 / C4), RBAC là yêu cầu thật trong mọi trường hợp: authN per-request
(OAuth 2.1 theo spec MCP), mapping identity → credential nguồn, và `Credentials` provider đổi
từ "env của process" sang "per-request" (ADR-0002 đã cam kết không chặn đường này).

## Alternatives Considered

### Alternative 1: Không có cột `visibility`, dựa hoàn toàn vào BR-003
- **Pros**: Không công ở v1; đúng với phạm vi đã thoả thuận.
- **Cons**: Khi cần RBAC phải **re-ingest toàn bộ** để lấy metadata quyền (thông tin quyền chỉ
  có lúc crawl, không suy ra được từ chunk đã lưu). Và không có cách nào biết corpus hiện tại
  có nội dung hạn chế hay không.
- **Why not**: Một cột `text` với default là chi phí gần bằng không so với một lần re-embed
  toàn bộ corpus.

### Alternative 2: Implement RBAC đầy đủ ngay ở v1
- **Pros**: Không có cửa sổ rủi ro nào.
- **Cons**: Cần identity per-request mà stdio **không có** (mỗi process là một người dùng);
  tức phải xây HTTP transport trước — đúng thứ Out of scope loại bỏ.
- **Why not**: Không khả thi về mặt kỹ thuật trên stdio và vượt phạm vi v1.

### Alternative 3: Filter theo `visibility` ngay ở v1 với một allowlist tĩnh trong env
- **Pros**: Có filter mà không cần identity per-request.
- **Cons**: Allowlist tĩnh cho mỗi cài đặt = cấu hình trùng lặp trên mọi máy, và nó vẫn không
  phải RBAC (không gắn với người dùng thật, người dùng tự sửa env được).
- **Why not**: Tạo cảm giác an toàn giả mà không thay đổi mô hình đe doạ.

## Consequences

### Positive
- Đường mở sang RBAC không đòi re-ingest (BR-004 được giữ với chi phí một cột).
- Biến R13 từ "rủi ro chờ tới lúc mở HTTP" thành một câu hỏi trả lời được ngay ở Phase 3.

### Negative
- Connector phải biết cách suy ra `visibility` cho từng nguồn — thêm việc cho Phase 3 và là
  chỗ dễ sai (suy ra sai ⇒ nhãn sai ⇒ filter tương lai sai).
- Giá trị `visibility` là **ảnh chụp lúc crawl**; quyền ở nguồn đổi sau đó thì nhãn cũ. Cần
  full reconcile để cập nhật, và điều này phải được nói rõ khi RBAC được bật.

### Risks & follow-ups
- **Cần PO quyết trước khi code Phase 3** (Phần 2 ở trên): corpus có được phép chứa nội dung
  `restricted` hay không. Câu trả lời đổi Phase 3 từ "không có RBAC" thành "RBAC là must-have",
  nên đây là một quyết định chặn, không phải một ghi chú.
- Quy tắc suy ra `visibility` cho từng connector **chưa được định nghĩa** (Confluence space
  permission, GitLab project visibility + member role). Là đầu ra của spike Phase 3, cùng với
  quy tắc dựng `source_id` đã nêu ở ADR-0012 A5.
- ADR này chưa có trong bảng ADR của `architecture.md` (bảng đó có 15 dòng) — cần thêm dòng
  thứ 16 khi architecture.md được cập nhật lần tới.
