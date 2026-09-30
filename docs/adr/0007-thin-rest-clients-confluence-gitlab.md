# ADR-0007: Thin REST client tự viết cho Confluence & GitLab

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa); auth mode cần xác nhận ở Gate B

## Context

Phase 1 cần Confluence (FR-001) và GitLab (FR-002) read-only. Có sẵn `atlassian-python-api`
và `python-gitlab`, nhưng cả hai đều là SDK đầy đủ **bao gồm mọi method ghi/xoá**
(`page.update()`, `project.issues.create()`, `mr.merge()`), tức là đưa một bề mặt mutating
lớn vào cùng process với server phải bảo đảm read-only tuyệt đối (BR-001/FR-014). Chúng cũng
là client đồng bộ (`requests`) nên không dùng được timeout/retry chung của ADR-0006.

## Decision

Viết thin client read-only cho từng nguồn trong `packages/mcp_confluence/.../client.py` và
`packages/mcp_gitlab/.../client.py`, dùng `httpx.AsyncClient` từ `mcp_common.http`; mỗi client
chỉ có method GET tương ứng các endpoint trong allowlist, và lớp gọi chung raise
`NotPermittedError` với bất kỳ HTTP method khác GET/HEAD.

Endpoint allowlist v1:
- Confluence: `/rest/api/content/search` (CQL), `/rest/api/content/{id}`,
  `/rest/api/content/{id}/child/page`, `/rest/api/space`, `/rest/api/content/{id}/body/export_view`
  (Server/DC v1). Nếu là Confluence Cloud: `/wiki/api/v2/pages`, `/wiki/api/v2/spaces`,
  `/wiki/rest/api/search`. Biến thể chọn bằng `MCP_CONFLUENCE_FLAVOR=server|cloud`.
- GitLab: `/api/v4/search`, `/api/v4/projects`, `/api/v4/projects/{id}`,
  `/api/v4/projects/{id}/search`, `/api/v4/projects/{id}/repository/{files,tree,commits}`,
  `/api/v4/projects/{id}/{merge_requests,issues,pipelines}`, `/api/v4/projects/{id}/pipelines/{id}/jobs`,
  `/api/v4/projects/{id}/jobs/{id}/trace`.

Auth: hỗ trợ hai chế độ qua `MCP_<SRC>_AUTH_MODE`: `bearer` (PAT — Confluence Server/DC,
GitLab PAT scope `read_api`) và `basic` (email + API token — Confluence Cloud).
**Chế độ mặc định cần user xác nhận ở Gate B** (phụ thuộc Confluence là Cloud hay Server/DC).

## Alternatives Considered

### Alternative 1: `atlassian-python-api` + `python-gitlab`
- **Pros**: Tiết kiệm nhiều thời gian implement; đã xử lý phân trang, edge case API.
- **Cons**: Mang theo toàn bộ API ghi/xoá vào process read-only; client sync; khó áp
  timeout/retry/redaction chung; test phải mock ở tầng `requests`.
- **Why not**: Mâu thuẫn trực tiếp với BR-001 lớp 1 của ADR-0003 — bề mặt mutating tồn tại
  trong runtime dù không expose ra tool.

### Alternative 2: SDK đầy đủ nhưng bọc bằng wrapper chặn method ghi
- **Pros**: Vẫn dùng được phân trang của SDK.
- **Cons**: Wrapper phải chặn theo tên method (dễ sót khi SDK nâng cấp), vẫn là sync client.
- **Why not**: Bảo đảm yếu hơn mà không bớt nhiều công.

### Alternative 3: Chỉ dùng GraphQL của GitLab
- **Pros**: Một endpoint, lấy đúng field cần.
- **Cons**: Search code (`blobs`) chỉ có ở REST; GraphQL cũng có mutation trên cùng endpoint
  nên allowlist theo endpoint không còn hiệu lực.
- **Why not**: Mất khả năng allowlist theo endpoint và thiếu tính năng search code.

## Consequences

### Positive
- Bề mặt mutating bằng 0 ngay ở tầng thư viện, allowlist kiểm chứng được trong unit test.
- Timeout, retry, redaction, envelope dùng chung với 7 server còn lại.
- Test dùng `respx`/`httpx.MockTransport` — nhanh, không cần network.

### Negative
- Phải tự implement phân trang (`_links.next` của Confluence, header `X-Next-Page` của GitLab)
  và chuẩn hoá lỗi 401/403/404/429.
- Phải xử lý hai flavor Confluence (Cloud vs Server/DC) — tăng công Phase 1.

### Risks
- Sai lệch so với API thật khi chỉ test bằng mock. Giảm thiểu: một bộ integration test có
  gắn nhãn `@pytest.mark.live`, chạy tay khi có VPN, không chạy trong CI mặc định; cùng với
  lệnh `doctor` kiểm tra thực tế trước khi bắt đầu implement (Open question 4).

## Amendments (sau design review 2026-10-01)

### A1 — Bỏ `body/export_view` khỏi allowlist Confluence
`export_view` **render macro phía server**: một số macro gọi ra hệ thống ngoài hoặc chạy việc
nặng, tức một endpoint "GET" vẫn có side effect quan sát được ở nguồn — đúng thứ ADR-0003 A3
cấm. Bỏ khỏi allowlist; FR-001 dùng `body.storage` (Server/DC) hoặc `body.view` và tự chuẩn
hoá sang markdown bằng `mcp_common.content` (vốn đã là trách nhiệm của nó).

Allowlist Confluence Server/DC sau sửa: `/rest/api/content/search`, `/rest/api/content/{id}`
(kèm `expand=body.storage`), `/rest/api/content/{id}/child/page`, `/rest/api/space`.

### A2 — Credential tự chứng minh read-only lúc khởi động (ADR-0003 A1)
`build_server()` từ chối serve nếu không xác minh được token là read-only:

| Nguồn | Kiểm lúc khởi động |
|---|---|
| GitLab | `GET /api/v4/personal_access_tokens/self` → `scopes ⊆ {read_api, read_repository}` |
| Confluence | current-user check + assert token không có quyền tạo nội dung |

Thêm hai endpoint này vào allowlist (chỉ dùng cho startup check, không expose thành tool).
Escape duy nhất: `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true`, log WARN mỗi lần khởi động.

### A3 — Tách client thành hai tầng + pagination tường minh (ADR-0012 A4)
`client.py` chỉ giữ transport + allowlist + timeout; các bound dành riêng cho tool
(`limit ≤ 100`, time range ≤ 31 ngày, `max_bytes`) chuyển sang `read_api.py`. Connector của
`mcp-ingest` dùng tầng transport, **không** dùng `read_api` — nếu không, crawl toàn bộ
Confluence/GitLab bị chính bound của tool chặn lại.

Con trỏ phân trang chốt tường minh để BE/QA không assert khác nhau: Confluence `_links.next`,
GitLab header `X-Next-Page`.

### A4 — Timeout theo bộ số đã chốt lại (ADR-0006 A2)
`read timeout` 7s (không phải 15s), tổng số lần thử 2 (1 retry) → 2 × (3 + 7) + 1s backoff =
21s < `MCP_TOOL_DEADLINE` 25s.
