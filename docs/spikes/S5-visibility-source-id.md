# Spike S5 — Quy tắc `visibility` và `source_id` theo từng connector (T-055)

Ngày: 2026-10-01. Đầu ra đóng phần "quy tắc suy ra" của ADR-0016 (R15) và `source_id` ổn định qua
ILM rollover của ADR-0012 A5. Mã tương ứng (thuần, đã test): `packages/mcp_ingest/src/mcp_ingest/identity.py`,
`tests/test_identity.py` (38 test). **Chưa kiểm chứng trên hệ thật**: Confluence/GitLab/OpenSearch
không tới được từ container của squad (spike S1) — hình dạng API bên dưới lấy từ tài liệu API công
khai, phải xác nhận bằng test `@pytest.mark.live` khi có VPN (T-070/T-071/T-072).

## Quyết định nền (user, 2026-10-01)

Corpus `kb` là **TEAM-ONLY** (ADR-0016 Phần 2, nhánh "không chứa `restricted`"). Hệ quả:

* **Không có RBAC per-user ở Phase 3.** T-067 **đóng — không áp dụng**.
* `mcp-ingest` **từ chối** document có `visibility != 'team'` ở stage `redact` (T-073, đợt sau),
  ghi `kb.ingest_failures{stage: redact, code: blocked_by_policy}`; document đó không bao giờ vào `kb.chunks`.
* Vai trò của connector (đợt này chỉ định nghĩa quy tắc): **gắn nhãn đúng, thiên về từ chối**, để
  T-073 có căn cứ chặn. Cột `documents.visibility` vẫn giữ (ADR-0016 Phần 1) — một câu SQL trả lời
  "corpus có nội dung hạn chế không" (kỳ vọng: 0 dòng `restricted`).

**Nguyên tắc: default-deny.** Chỉ gắn `team` khi *chứng minh được* cả team đọc được. Không xác định
được, API lỗi, thiếu quyền đọc metadata, giá trị lạ → `restricted`.

## 1. Quy tắc `visibility`

### 1.1 Confluence (Cloud) — `confluence_visibility(...)`

`team` khi **tất cả** điều kiện sau đúng, ngược lại `restricted`:

| # | Điều kiện | Nguồn dữ liệu (cần xác nhận live) | Vì sao |
|---|---|---|---|
| 1 | `space.key` nằm trong allowlist của operator `MCP_INGEST_CONFLUENCE_TEAM_SPACES` | cấu hình | Quyền space là cấu hình của người quản trị Confluence, API không cho một dấu hiệu "cả team đọc được" đáng tin; allowlist là lời **khai báo có chủ đích** của operator, kiểm chứng được (default rỗng = không gì là team) |
| 2 | `space.type == "global"` (không phải `personal`) | `GET /wiki/rest/api/space/{key}` | Space cá nhân không phải nội dung của team dù có nằm trong allowlist nhầm |
| 3 | Page **không** có read-restriction | `GET /wiki/rest/api/content/{id}/restriction/byOperation/read` (user/group rỗng) | Restriction page là cơ chế "chỉ vài người" |
| 4 | **Không tổ tiên nào** có read-restriction | duyệt `ancestors` của page, cache theo id tổ tiên | Restriction **được thừa kế** xuống page con; chỉ xem page là bỏ sót |
| — | Không xác định được (3) hoặc (4) (`None`) | — | không chứng minh được ⇒ `restricted` |

Khuyến nghị: tài khoản crawl là **thành viên thường** của team (không admin/site-admin) — để một
nhãn sai cũng không thể kéo về trang mà team không đọc được (phòng thủ chiều sâu; nhãn là lớp thứ hai).

### 1.2 GitLab — `gitlab_visibility(...)`

| Điều kiện | Kết quả |
|---|---|
| `kind in {issue, mr}` và `confidential = true` | `restricted` |
| tính năng liên quan (`repository_access_level` cho `blob`, `issues_access_level`, `merge_requests_access_level`) khác `enabled` (tức `private`/`disabled`/không rõ) | `restricted` — "members only" ≠ cả team |
| `project.visibility = public` | `team` |
| `project.visibility = internal` | `team` **mặc định**; `MCP_INGEST_GITLAB_INTERNAL_IS_TEAM=false` ⇒ `restricted` (instance có user ngoài team) |
| `project.visibility = private` | `team` **chỉ khi** path project thuộc `MCP_INGEST_GITLAB_TEAM_PROJECTS` (so khớp theo **segment** path: `pay` khớp `pay/x`, **không** khớp `payroll/x`) **và** access level của token crawl ≥ Reporter (20) |
| `private` mà không khai báo, hoặc token là Guest/không rõ | `restricted` |
| giá trị lạ / `None` | `restricted` |

Lý do Reporter: Guest không đọc được code của project private ⇒ không thể khẳng định gì về nhãn.
"Role của member" trong plan được hiểu là role của **token crawl** (người thực thi), vì quyền từng
người dùng cuối không được mô hình hoá ở Phase 3.

### 1.3 OpenSearch — `opensearch_visibility(...)`

Connector **mặc định tắt** (ADR-0012 A5). Khi bật, `team` **chỉ** cho alias nằm trong
`MCP_INGEST_OPENSEARCH_INDICES` (so sánh sau khi bỏ hậu tố rollover, xem 2.3); mọi thứ khác
`restricted` (và không bao giờ được crawl). OpenSearch không cho đọc ngược ACL từng document, nên
allowlist là lời **cam kết** của operator rằng index đó (runbook/postmortem) là nội dung cả team
được xem; index có document-level security hoặc chứa PII/secret **không** được đưa vào allowlist.

## 2. Quy tắc `source_id` (khoá UNIQUE `(source_type, source_id)` — bản lề của FR-012/AC-003)

| Nguồn | `source_id` | Ổn định qua | Ví dụ |
|---|---|---|---|
| Confluence | `page.id` (số) | đổi tên, chuyển space, sửa nội dung (đổi `title`/`container`/`source_uri` chỉ UPDATE metadata) | `123456` |
| GitLab file | `<project_id>:blob:<đường dẫn>` (nhánh mặc định) | đổi tên/chuyển namespace project (dùng **id số**, không dùng path) | `42:blob:src/app/main.py` |
| GitLab MR / issue | `<project_id>:mr:<iid>` / `<project_id>:issue:<iid>` | đổi tên project | `42:mr:7` |
| OpenSearch | `<alias>:<_id>` | **ILM rollover**, index theo ngày, backing index của data stream | `postmortems:doc-abc` |

Ghi chú:

* File GitLab đổi tên/di chuyển trong repo = id mới; bản cũ biến mất khỏi lần crawl nên bị
  tombstone ở full reconcile (hành vi mong muốn: nội dung cũ không còn tồn tại ở đường dẫn đó).
* OpenSearch: `opensearch_alias()` chỉ bỏ các dạng **rollover thật**: tiền tố `.ds-` (data stream),
  hậu tố `-NNNNNN` (ILM), hậu tố ngày `-YYYY.MM[.DD]` / `-YYYY-MM[-DD]`. Tên như `postmortems-v2`,
  `runbooks-2026-q3` được giữ nguyên.
* Ổn định của `source_id` OpenSearch **phụ thuộc `_id` do producer đặt** (cùng `_id` khi document được
  ghi lại sang backing index mới). Index dùng `_id` tự sinh không có identity qua rollover ⇒ **không
  phù hợp để ingest** (đúng lý do ADR-0012 A5 tắt mặc định). Connector nên có tuỳ chọn
  `id_field` (đợt T-072) và từ chối index không đáp ứng.

Ví dụ rollover có test (`test_S5_opensearch_source_id_is_stable_across_ilm_rollover`):
`postmortems-000001`/`postmortems-000002` + `doc-abc` → cùng `postmortems:doc-abc`;
`.ds-app-logs-2026.09.30-000012` → alias `app-logs`.

## 3. Case "quyền đổi ở nguồn sau khi crawl"

`visibility` là **ảnh chụp lúc crawl** (ADR-0016). Hệ quả với corpus team-only:

| Thay đổi ở nguồn | Phát hiện khi nào | Hành vi bắt buộc của pipeline (T-073/T-075, đợt sau) |
|---|---|---|
| **team → restricted** (page bị đặt restriction, project đổi sang private, issue thành confidential) | GitLab: project-level được kiểm lại **mỗi run** (1 call/project đã index) và `confidential`/`updated_at` đổi ⇒ thấy ngay ở incremental. **Confluence: đổi restriction không chắc đổi `lastModified`** ⇒ incremental không thấy; chỉ thấy ở **full reconcile** (kế hoạch: hằng ngày) | Nhãn được **tính lại cho mọi document đã thấy, kể cả khi hash-skip** (là metadata, luôn UPDATE như citation metadata). Nếu document đang có trong kb mà nhãn mới là `restricted` ⇒ **xoá chunk + tombstone** trong cùng transaction và ghi `ingest_failures{redact, blocked_by_policy}`. Không được chỉ "bỏ qua lần này" |
| **restricted → team** | Full reconcile / `run --retry-failed` | Document từng bị từ chối được ingest bình thường; không liên quan bảo mật, chỉ trễ |
| Space bị gỡ khỏi allowlist | Mỗi run (cấu hình, không cần API) | Như team→restricted: purge |

**Cửa sổ rủi ro còn lại (nêu thẳng):** một page Confluence bị đặt restriction mà không đổi nội dung
vẫn nằm trong `kb` và vẫn trả được qua `kb_semantic_search` **cho tới full reconcile kế tiếp**
(≤ chu kỳ reconcile, ứng viên 24 giờ). Giảm thiểu: (a) full reconcile Confluence đủ dày theo mức
chấp nhận của PO (Open question 5); (b) lệnh vận hành `mcp-ingest prune`/xoá theo `source_id` khi
được báo (T-081); (c) tài khoản crawl quyền thấp (mục 1.1) để phạm vi rò chỉ là phần team vốn đọc
được; (d) PO cần biết cửa sổ này khi chấp nhận "team-only" thay cho RBAC. **Khi RBAC được bật sau
này, phải nói rõ nhãn là ảnh chụp** (ADR-0016).

## 4. Việc cho đợt sau / SA / PO

* **T-070/T-071/T-072**: gọi `identity.py`; lấy dữ kiện (space type, restriction, project
  visibility, `*_access_level`, `confidential`) từ `client.py` của package nguồn; thêm test live xác
  nhận hình dạng API ở mục 1 (đặc biệt `restriction/byOperation/read` và restriction thừa kế).
* **T-073**: từ chối `visibility != 'team'`; purge document đã có khi nhãn đổi (mục 3).
* **Cấu hình mới** (đã ghi trong `.env.example`, đợt sau mới đọc): `MCP_INGEST_CONFLUENCE_TEAM_SPACES`,
  `MCP_INGEST_GITLAB_TEAM_PROJECTS`, `MCP_INGEST_GITLAB_INTERNAL_IS_TEAM`.
* **SA — ADR-0016**: ghi nhận Phần 2 = "corpus team-only" (quyết định user 2026-10-01), T-067 đóng,
  các quy tắc trên là phần "quy tắc suy ra" còn thiếu, và cửa sổ rủi ro ở mục 3. Cập nhật bảng ADR
  của `architecture.md` (dòng 16).
* **PO**: xác nhận chấp nhận cửa sổ rủi ro ở mục 3, và danh sách space/project team (allowlist).
