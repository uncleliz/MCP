# ADR-0021: Company MCP gateway-boundary IN-PROCESS (giữ stdio) — một choke point cho routing/permission/audit/rate-limit

**Date**: 2026-10-01
**Status**: accepted — CHG-001 Option C (ADR-0017), DP1 = gateway mỏng in-process giữ stdio
**Deciders**: SA (squad-sa) đề xuất; CTO quyết trong scope CHG-001 Option C; NFR-005 là invariant CEO giữ ở Gate 1

## Context

Spec §6 đòi trách nhiệm "Company MCP Gateway": routing Knowledge/Live, auth-context,
policy/permission enforce, audit, rate-limit. CEO Gate 1 (ADR-0017) chọn **Option C**: thỏa
§6 **ở tầng process, GIỮ NFR-005 stdio-only** — **không** service HTTP (ContextForge của
Option B bị loại). ADR-0003 Alt 3 đã bác "proxy chung" vì phá stdio + SPOF; Option C không
mâu thuẫn vì gateway-boundary là **thư viện in-process**, không mở cổng mạng.

## Decision

Một module `mcp_gateway` (trong `mcp_common` hoặc package riêng dùng chung) là **boundary
in-process** mọi tool-call Knowledge/Live đi qua, cung cấp:
- **routing/discovery** — hợp nhất tool-surface Knowledge + Live dưới một runtime stdio;
- **auth-context** — danh tính người gọi ở v1 = chủ của process (per-user stdio, ADR-0016);
  cấu trúc để v1.1 đổi sang per-request khi mở HTTP (BR-004/C4) **không viết lại policy**;
- **permission enforce server-side** — một hàm `enforce_permission(ctx, candidates)` chạy
  **trước** context assembly (ADR-0016, spec §24/§43), default-deny. Đây là **MỘT choke point
  duy nhất cho CẢ 8 tool nội dung** của tầng Knowledge (`search_company_knowledge`,
  `get_jira_context`, `search_code`, `get_service`, `get_repository`, `find_related_knowledge`,
  `get_knowledge_summary`, `get_document_version`) — mỗi tool chạy đúng một `enforce_permission`
  (một query `document_grants`) trước khi trả content/`source_uri`/provenance, không tool nào là
  đường vòng default-allow quanh choke point. Corpus team-only (ADR-0016 A1) là **defence-in-depth
  sau** choke point này, **không** phải rào cản duy nhất (khớp code sau R-C-001: errors E-…-007);
- **audit** — append-only log mỗi tool-call (tool, args-hash, verdict, latency) ra stderr JSON;
- **rate-limit** — token-bucket in-process theo tool/nguồn (chống bão call).

**Không mở cổng mạng** (giữ NFR-005); không SPOF service; transport vẫn stdio. Khi mở HTTP+SSE
v1.1, chỉ thêm **transport adapter trước** `mcp_gateway` — policy/permission/audit không đổi.
Permission filter (gate này) và grounding gate (ADR-0018) là **hai gate khác mục đích**:
permission chạy **trước** (loại tài liệu không-quyền khỏi ứng viên), grounding gate chạy **sau**
(đóng verdict FACT/UNKNOWN/CONFLICT). Mỗi cái vẫn là **một** choke point của riêng nó (L-001).

## Alternatives Considered

### Alternative 1: Service HTTP OSS (IBM ContextForge) — Option B
- **Pros**: SSO/RBAC/audit/rate-limit/OTel sẵn có.
- **Cons**: **phá NFR-005** (service HTTP `:4444`) + SPOF + spike nhúng-stdio UNVERIFIED + lock-in; operating burden nặng cho công ty một người.
- **Why not**: CEO giữ NFR-005 ở Gate 1 (ADR-0017); Option B bị loại.

### Alternative 2: Hoãn gateway sang v1.1 — Option A
- **Pros**: Deviation nhỏ nhất; permission đặt thẳng trong mỗi tool.
- **Cons**: Permission rải trong từng tool ⇒ dễ sót khi thêm tool (không một choke point); thiếu routing/audit/rate-limit tập trung; phải thêm cả tầng sau.
- **Why not**: Option C đặt sẵn boundary mà vẫn giữ stdio, chi phí thêm nhỏ (4–6 ngày-agent) — strategic fit tốt hơn.

### Alternative 3: Permission trong Postgres (RLS)
- **Pros**: Enforce tại DB.
- **Cons**: v1 không có identity per-request để map vào RLS (stdio = một người/process); corpus team-only (ADR-0016 A1) nên RLS per-user chưa áp dụng.
- **Why not**: Chưa cần tới khi mở remote; `enforce_permission` in-process đủ cho team-only + đặt sẵn đường.

## Consequences

### Positive
- Trách nhiệm §6 thỏa mà **giữ NFR-005**; không service/vendor/cổng mạng mới.
- **Một** choke point cho permission (L-001), tier-wide cho cả 8 tool nội dung Knowledge + đường mở HTTP v1.1 chỉ đổi transport.

### Negative
- Phải tự viết audit/rate-limit/policy ở mức cần cho local (không có sẵn như ContextForge); SSO đầy đủ để v1.1.

### Risks
- Gateway-boundary tự viết có thể sót case → giảm thiểu bằng adversarial test (caller không-quyền không thấy `restricted` trước context-pack — L-001) + test "mọi tool-call Knowledge/Live đi qua gateway".
- Hai gate (permission trước, grounding sau) dễ bị nhầm thành một → tài liệu rõ ràng; mỗi gate có test choke-point riêng.

## Links
- ADR-0002 (stdio transport-agnostic), ADR-0003 (read-only choke point; Alt 3 bác proxy HTTP),
  ADR-0016 (permission/visibility default-deny), ADR-0017 (CHG-001 Option C — DP1), ADR-0018 (grounding gate).
  Spec §6, §24/§43. NFR-005, BR-004. Lessons L-001.
