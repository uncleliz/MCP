# ADR-0019: Jira là nguồn thứ 10 — thin REST read-only, flavor Cloud/Server-DC

**Date**: 2026-10-01
**Status**: accepted — CHG-001 Option C (ADR-0017), tiếp nối khuôn ADR-0007
**Deciders**: SA (squad-sa) đề xuất; CTO quyết trong scope CHG-001 Option C

## Context

CHG-001 Option C (ADR-0017, CEO Gate 1) thêm **Jira làm nguồn thứ 10** — vừa là nguồn
ingest cho Company Knowledge (issue/epic/sprint → `kb`), vừa là **Live MCP** để tra "trạng
thái công việc hiện tại" không qua snapshot. Bất biến bắt buộc: **read-only tuyệt đối**
(BR-001/NFR-001, L-001), **stdio** (NFR-005), bề mặt ghi = 0. Jira có hai flavor API khác
nhau về phân trang và endpoint: **Cloud** (`/rest/api/3`, cursor `nextPageToken`) và
**Server/Data Center** (`/rest/api/2`, offset `startAt`/`maxResults`). Nền đã có khuôn
thin-REST tự viết cho Confluence/GitLab (ADR-0007) với hai tầng `client.py`/`read_api.py`.

## Decision

Thêm `mcp-jira` theo **đúng khuôn ADR-0007**: thin REST client tự viết trên `httpx`
(không SDK `jira`/`atlassian-python-api` — chúng mang theo toàn bộ bề mặt ghi), dùng chung
`mcp_common` (transport allowlist GET/HEAD, timeout budget ADR-0006, envelope ADR-0004,
redaction ADR-0015). Hai tầng: `client.py` (transport + allowlist + flavor split) dùng chung
cho **cả Live MCP và connector ingest**; `read_api.py` (bound của tool). Flavor
(`cloud|server`) chọn bằng `MCP_JIRA_FLAVOR` + auto-detect từ base URL; phân trang đóng gói
trong `CursorField` opaque (ADR-0004) nên tool không lộ `nextPageToken` vs `startAt`.
Incremental ingest theo JQL `updated >= last_run` (biên inclusive `>=`, trừ ε như ADR-0012 A2);
**xoá/di chuyển** phát hiện bằng **full-reconcile tombstone** (ADR-0011 A1 + safety-valve
ADR-0012 A3) vì Jira không có feed xoá đáng tin. `visibility` suy ra default-deny (ADR-0016 A2):
project/issue có security-level hoặc `restricted` → `restricted` (bị từ chối ingest team-only).

Tool Live (read-only): `jira_search_issues` (JQL bounded), `jira_get_issue`,
`jira_list_projects`, `jira_get_sprint` / `jira_list_board_sprints`. Mọi tool `x-readonly: true`,
`x-side-effects: none`; **không** có `create/transition/comment`.

## Alternatives Considered

### Alternative 1: `atlassian-python-api` / `jira` SDK
- **Pros**: Nhanh để viết; cover nhiều endpoint.
- **Cons**: Mang theo toàn bộ API ghi (transition, comment, worklog) vào runtime → bề mặt ghi > 0,
  phá L-001 (guarantee read-only khó assert khi SDK có hàm ghi); auth/flavor khó kiểm soát ở tầng transport.
- **Why not**: Giống lý do ADR-0007 bác SDK cho Confluence/GitLab.

### Alternative 2: Chỉ ingest Jira (không Live MCP)
- **Pros**: Ít tool hơn.
- **Cons**: "Trạng thái công việc hiện tại" (sprint đang chạy, issue vừa đổi) là Live-vs-Knowledge
  theo spec §42 (source authority: current work status → Jira); snapshot cũ không đáp ứng.
- **Why not**: Mất giá trị chính của Jira trong grounding/conflict.

### Alternative 3: Một tool `jira_jql(jql)` tuỳ ý
- **Pros**: Linh hoạt tối đa.
- **Cons**: JQL tuỳ ý = bề mặt tấn công + khó bound (giống `postgres_query(sql)` đã bị bác ở ADR-0008);
  khó cap kết quả/time-range.
- **Why not**: Vi phạm nguyên tắc bound của `read_api.py` (ADR-0007 A3).

## Consequences

### Positive
- Nguồn thứ 10 vào cùng khuôn, cùng choke point read-only + envelope; QA tái dùng `assert_readonly_tool_surface`.
- Flavor split đóng trong `client.py` nên tool/contract không đổi khi khách dùng Cloud hay Server.

### Negative
- Jira không có feed xoá ⇒ phụ thuộc full-reconcile để tombstone (độ trễ ≤ chu kỳ reconcile — như Confluence ADR-0016 A3).
- Hai flavor = hai đường phân trang/endpoint phải test riêng (`@pytest.mark.live` cho mỗi flavor).

### Risks
- Hình dạng API Cloud vs Server khác nhau ở vài field (vd `renderedFields`) — xác nhận bằng live test; chưa chạy được tới khi có VPN/credential (nối spike S1).
- JQL incremental bỏ sót nếu `updated` không đổi khi chỉ security-level đổi — giảm thiểu bằng reconcile tính lại `visibility` mọi run (ADR-0016 A2).

## Links
- Khuôn: ADR-0007 (thin REST), ADR-0004 (envelope), ADR-0006 (timeout), ADR-0015 (redaction),
  ADR-0011/0012 (tombstone/checkpoint/safety-valve), ADR-0016 (visibility default-deny), ADR-0017 (CHG-001 Option C).
