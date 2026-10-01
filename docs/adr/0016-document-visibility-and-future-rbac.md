# ADR-0016: Cột `visibility` và đường mở sang RBAC per-user khi chuyển remote

**Date**: 2026-10-01
**Status**: accepted (2026-10-01) — user chọn **corpus TEAM-ONLY** (Phần 2, nhánh "không chứa `restricted`"); không có RBAC per-user ở v1. Xem Amendments A1–A3
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
- ~~ADR này chưa có trong bảng ADR của `architecture.md`~~ — đã có dòng 16 (reconcile 2026-10-01).
- ~~Cần PO quyết Phần 2~~ — đã quyết (A1). ~~Quy tắc suy ra `visibility` chưa định nghĩa~~ — đã
  định nghĩa (A2).

## Amendments (quyết định user + spike S5, 2026-10-01)

### A1 — Phần 2 đã quyết: corpus `kb` là TEAM-ONLY
User quyết ngày 2026-10-01: corpus `kb` **chỉ** chứa nội dung cả team đọc được. Hệ quả:
- **Không có RBAC per-user ở Phase 3.** Task T-067 (RBAC per-user) **đóng — không áp dụng**.
- `mcp-ingest` **từ chối** mọi document có `visibility != 'team'` ở stage `redact` (T-073): ghi
  `kb.ingest_failures{stage: redact, code: blocked_by_policy}`, document không bao giờ vào
  `kb.chunks`. Giả định BR-003 trở thành ràng buộc kiểm chứng được:
  `SELECT count(*) FROM kb.documents WHERE visibility <> 'team' AND deleted_at IS NULL` phải = 0.
- Cột `visibility` (Phần 1) giữ nguyên — vẫn là đường mở sang RBAC khi chuyển remote (BR-004/C4),
  khi đó RBAC là yêu cầu thật như đoạn cuối của Decision.

### A2 — Quy tắc suy ra `visibility`: **default-deny** (spike S5)
Nguồn: [`docs/spikes/S5-visibility-source-id.md`](../spikes/S5-visibility-source-id.md);
code thuần `packages/mcp_ingest/src/mcp_ingest/identity.py` + test. Nguyên tắc: chỉ gắn `team`
khi **chứng minh được** cả team đọc được; không xác định được, API lỗi, thiếu quyền đọc metadata,
giá trị lạ ⇒ `restricted` (⇒ bị từ chối theo A1).
- **Confluence (Cloud)** — `team` khi **tất cả**: space thuộc allowlist
  `MCP_INGEST_CONFLUENCE_TEAM_SPACES` (rỗng mặc định ⇒ không gì là team); `space.type == global`
  (không `personal`); page **không** có read-restriction; **không tổ tiên nào** có
  read-restriction (restriction thừa kế). Không xác định được restriction ⇒ `restricted`.
- **GitLab** — issue/MR `confidential` ⇒ `restricted`; feature access level liên quan
  (`repository_access_level` / `issues_access_level` / `merge_requests_access_level`) khác
  `enabled` ⇒ `restricted`; `public` ⇒ `team`; `internal` ⇒ `team` trừ khi
  `MCP_INGEST_GITLAB_INTERNAL_IS_TEAM=false`; `private` ⇒ `team` **chỉ khi** project thuộc
  `MCP_INGEST_GITLAB_TEAM_PROJECTS` (khớp theo segment path) **và** token crawl ≥ Reporter;
  còn lại / giá trị lạ ⇒ `restricted`.
- **OpenSearch** (connector mặc định tắt, ADR-0012 A5) — `team` chỉ cho alias nằm trong
  `MCP_INGEST_OPENSEARCH_INDICES`; mọi thứ khác `restricted` và không được crawl.
- Nhãn được **tính lại ở mọi run cho mọi document đã thấy, kể cả khi hash-skip** (là metadata,
  như citation metadata ở ADR-0012 A4). Document đang có trong `kb` mà nhãn mới là `restricted`
  (hoặc space/project bị gỡ khỏi allowlist) ⇒ **xoá chunk + tombstone trong cùng transaction** và
  ghi `ingest_failures{redact, blocked_by_policy}`; không được chỉ "bỏ qua lần này" (T-073/T-075).
- Khuyến nghị vận hành: tài khoản/token crawl là **thành viên thường** của team (không admin),
  để một nhãn sai cũng không kéo về được nội dung team vốn không đọc được (phòng thủ chiều sâu).
- Quy tắc `source_id` ổn định (Confluence `page.id`; GitLab `<project_id>:blob|mr|issue:…`;
  OpenSearch `<alias>:<_id>`) cũng chốt ở S5 — đóng phần còn mở của ADR-0012 A5.
- Chưa kiểm chứng trên hệ thật (S1: nguồn không tới được từ container); hình dạng API phải được
  xác nhận bằng test `@pytest.mark.live` (T-070/T-071/T-072).

### A3 — Rủi ro tồn dư được chấp nhận có ý thức
`visibility` là **ảnh chụp lúc crawl**. Với Confluence, đặt read-restriction lên một page
**không chắc đổi `lastModified`**, nên incremental run không thấy: một page **bị đặt restriction
mà không đổi nội dung vẫn nằm trong `kb` và vẫn trả được qua `kb_semantic_search` cho tới lần
full reconcile kế tiếp** (≤ chu kỳ reconcile; ứng viên 24h). GitLab được kiểm project-level mỗi
run nên cửa sổ này nhỏ hơn nhiều. Giảm thiểu: (a) chu kỳ full reconcile Confluence theo mức PO
chấp nhận (Open question 5); (b) lệnh vận hành xoá theo `source_id`/`prune` khi được báo (T-081);
(c) token crawl quyền thấp (A2) để phạm vi rò chỉ là nội dung team vốn đọc được. PO cần xác nhận
chấp nhận cửa sổ này cùng danh sách allowlist space/project (follow-up, không chặn code).
