# Decisions & change ledger — mcp-data-platform

> Append-only. Newest entries at the bottom. Only the Delivery Manager and the CTO write here.

---

## CHG-001 — Company Knowledge layer (Gateway + Hybrid-RAG + Jira) bổ sung lên nền 9-nguồn hiện tại

- **Recorded:** 2026-10-01T16:43+07:00 by Delivery Manager
- **Requested by:** CEO
- **Kind:** Kind 2 — change to a feature already in progress (per squad-delivery-manager.md §change)
- **Source:** `company-knowledge-mcp-spec.md` v1.0 (CEO đã nghiên cứu và muốn áp dụng)
- **CEO decisions (2026-10-01):**
  1. Bổ sung tại chỗ vào `mcp-data-platform` (KHÔNG tạo feature mới).
  2. Giữ nguyên 9 nguồn hiện tại; bổ sung nguồn mới + các tầng mới.
  3. Layout migration đã chạy (`migrate-layout.sh --force`, 11 file, 0 conflict).

### What changes (so với scope đã approved ở Gate A/B)

Hệ hiện tại: Claude ↔ 9 MCP server read-only độc lập qua stdio; pipeline ingest/embedding →
Postgres+pgvector cho semantic search. Spec mới thêm một **tầng Company Knowledge** lên trên:

| # | Hạng mục mới | Có trong scope cũ? |
|---|---|---|
| C1 | **Jira** làm nguồn mới (ingestion + Live MCP) | Không |
| C2 | **Company MCP Gateway** (auth, authz, policy, audit, routing, rate-limit) | Không |
| C3 | **Hybrid-RAG retrieval engine**: vector + keyword + metadata + relationship + RRF + reranker + context compression + context-pack | Chỉ có semantic search đơn giản |
| C4 | **Knowledge MCP** với tool nghiệp vụ (search_company_knowledge, get_service, get_repository, search_code, get_jira_context, find_related_knowledge, get_knowledge_summary, get_document_version) | Không |
| C5 | **Live MCP** tách biệt + knowledge-vs-live decision + offline-source behavior + freshness model | Mọi query hiện đều live |
| C6 | **Document versioning** (document_versions, lịch sử, rollback) | Không |
| C7 | **Entities + relationships** (graph nghiệp vụ) | Không |
| C8 | **Knowledge summaries** (consolidated per-entity) | Không |
| C9 | **Permission enforcement server-side** (document_permissions, filter trước context assembly) | v1 giả định mọi người quyền như nhau |
| C10 | Hallucination/conflict control, source authority config, provenance mở rộng | Một phần (provenance/trace đã có) |

### Deviation assessment (DM sơ bộ — CTO xác nhận)

- Ngưỡng cấu hình: `DEVIATION_THRESHOLD_PCT=10`, `ESCALATE_COST_PCT=15`, `ESCALATE_SCHEDULE_PCT=20`.
- Change thêm 1 nguồn mới (Jira) + ≥2 kiến trúc hệ thống lớn chưa tồn tại (Gateway, Hybrid-RAG engine)
  + thay đổi mô hình truy cập cốt lõi (gateway thay vì stdio trực tiếp) + 4 domain dữ liệu mới
  (versions, entities/relationships, summaries, permissions).
- **DM đánh giá: deviation ≫ 10% → cần ADR chi tiết + CEO approval ở Gate 1 (không phải stale re-run nhỏ).**
- Tái dùng được từ nền hiện tại: connector Confluence/GitLab, Postgres+pgvector store, pipeline
  ingest/embedding, mô hình chunk/embedding, read-only defense-in-depth.

### Constraint hiện tại cần CTO cân nhắc khi size

- Feature đang ở stage **review (round 1, changes_requested)**, Gate C pending, chưa merge vào main.
  Còn 4 blocking review item (R-001..R-004 backend) + R-005 (QA re-run cần Docker) chưa đóng.
- Câu hỏi quan trọng CTO phải trả lời khi size: finish Gate C của scope cũ **trước** rồi mới mở
  phase mới cho CHG-001, hay gộp CHG-001 vào trước khi go-live? (ảnh hưởng cost/schedule baseline).

### Status

- [x] CTO (mode `blocked`) size CHG-001 → **LARGE**, deviation 100/100, escalate (D-001, 2026-10-01T16:44).
- [x] DM escalate → CEO Gate 1 (2026-10-01T16:45).
- [x] **CEO Gate-1 decision (2026-10-01T16:50): Option A** — đóng Gate C của scope 9-nguồn hiện tại
  TRƯỚC (xử lý R-001..R-005, merge, go-live), rồi mở CHG-001 thành một plan baseline Gate-1 MỚI
  trên cùng feature, chia epic E1..E8, với SA ra ≥3 options cho gateway/reranker/relationship-store
  + ADR-deviation bắt buộc. CHG-001 **chưa bắt đầu** cho tới khi scope cũ go-live.
- [ ] Luồng 1 (hiện tại): resume review scope cũ → đóng R-001..R-005 → Gate C → merge/go-live.
- [ ] Luồng 2 (sau go-live): CHG-001 Gate-1 (discovery → options → plan-approval) → build E1..E8.

### CEO Gate-1 note — CHG-001 (Option A)

CEO chọn A. CHG-001 được chấp nhận về nguyên tắc nhưng **xếp sau** lần go-live của scope 9-nguồn.
Không gộp vào go-live đầu. Khi scope cũ đã live, DM sẽ mở CHG-001 như một Gate-1 riêng (chưa phải
approval build — approval build của CHG-001 sẽ diễn ra ở Gate 1 của chính nó sau khi SA ra options
và CTO plan-review). Baseline cost/schedule của CHG-001 chưa chốt (chờ options của SA).

**CTO resolution (2026-10-01T16:44+07:00 — xem D-001 bên dưới):** **LARGE → ESCALATE to CEO Gate 1.**
CHG-001 KHÔNG phải stale re-run; đây là mở rộng scope lớn vượt option đã duyệt. DM escalate CEO Gate 1
với khuyến nghị phân rã thành epic/phase trên nền hiện tại; đóng Gate C scope cũ (9-nguồn read-only)
TRƯỚC rồi mới mở phase CHG-001. SA viết ADR-deviation + ≥3 options cho các điểm chốt (gateway, reranker,
relationship store). Chi tiết trong D-001.

---

## D-001 · blocked · 2026-10-01T16:44+07:00

(SIZE change CHG-001)

- Decision: ESCALATE (→ CEO Gate 1). Deviation verdict: **LARGE**. KHÔNG route stale re-run.
- Tier: large (giữ nguyên; CHG-001 không hạ tier)
- Evidence: `records/decisions.md#CHG-001`, `company-knowledge-mcp-spec.md`, `4-design/architecture.md`, `5-plan/implementation-plan.md`, `state.json`, `.kiro/squad/config.env`, ADR-0016 —
  - `records/decisions.md#CHG-001` (change C1..C10 + DM deviation assessment)
  - `company-knowledge-mcp-spec.md` v1.0 §6 Gateway, §9–14 Hybrid-RAG, §15–24 schema (versions/entities/relationships/summaries/permissions), §28 Jira, §43 security server-side
  - `4-design/architecture.md` "Context & constraints" C1..C7 + ADR-0016 (hiện: 9 server read-only stdio, semantic search đơn giản, no RBAC per-user, kb team-only)
  - `5-plan/implementation-plan.md` (86 task, toàn bộ Owner=BE, phủ scope 9-nguồn)
  - `state.json` (stage=review, gates.C=pending, R-001..R-005 chưa đóng, chưa merge)
  - `.kiro/squad/config.env` DEVIATION_THRESHOLD_PCT=10, ESCALATE_COST_PCT=15, ESCALATE_SCHEDULE_PCT=20
  - skill `squad-baselines` (thang điểm deviation), `squad-decision-rights` §escalate

### Deviation score (vs plan baseline đã duyệt Gate A/B)

> Baseline platform/business còn ở trạng thái template TBD → baseline hiệu lực là **option đã duyệt tại
> Gate A/B** cho mcp-data-platform (9 MCP server read-only stdio + pipeline ingest/pgvector, semantic
> search đơn giản, no RBAC per-user). CHG-001 được chấm so với baseline đó.

| Loại deviation (hard) | Điểm | Lý do |
|---|---|---|
| New architecture pattern | 20 | Company MCP Gateway (auth/authz/policy/audit/routing/rate-limit) + tách Knowledge/Live/Action MCP → đổi mô hình truy cập từ "stdio trực tiếp, mỗi nguồn một process, không service phụ" (C3) sang gateway-fronted. Vi phạm trực tiếp C3/NFR-005 của baseline. |
| New architecture pattern | 20 | Hybrid-RAG engine (vector+keyword+metadata+relationship+RRF+reranker+context-compression+context-pack) thay cho "semantic search đơn giản" — một retrieval subsystem mới, không chỉ là thêm tool. |
| Breaking change tới contract đang dùng | 30 | Chuyển access model qua gateway thay đổi bề mặt MCP mà client (Claude Desktop/Code đăng ký 9 server stdio) đang dựa vào; permission server-side (§43) đảo ngược giả định C6 "no RBAC per-user / quyền thừa hưởng tại máy user". |
| New language/framework/datastore domain | 25 | 4 domain dữ liệu mới trong Postgres chưa có trong schema đã duyệt: `document_versions`, `entities`+`relationships` (graph nghiệp vụ), `knowledge_summaries`, `document_permissions`; + stack mới gợi ý (FastAPI gateway, reranker model). |
| New external source | — | Jira (ingestion + Live MCP): nguồn thứ 10, ngoài 9 nguồn baseline. (Tính như mở rộng scope nguồn; không phải vendor trả phí mới nên không cộng 40, nhưng là Must-have mới.) |
| **Soft (CTO judgement)** | +12 | Tái dùng được kha khá (connector Confluence/GitLab, pgvector store, pipeline ingest/embedding, chunk model, read-only defense) nhưng drift kiến trúc rất lớn: thêm 5–6 tầng hệ thống mới chồng lên nền hiện tại, đảo giả định C3/C6, retrieval & live/knowledge split là subsystem mới. |
| **Tổng (cap 100)** | **100 (cap)** | ≫ ngưỡng 10. |

**Mọi hard deviation đơn lẻ ở trên đã tự vượt ngưỡng 10%.** Deviation = LARGE là hiển nhiên.

### Escalation rules đã fire (squad-decision-rights §escalate)

- **Rule 1** — Must-have mới (Jira làm nguồn; permission server-side; versioning) + công việc **drift ra ngoài option đã duyệt** (gateway, Hybrid-RAG, graph). → ESCALATE.
- **Rule 2** — Thêm 5–6 tầng kiến trúc mới + nguồn mới + retrieval engine → cost/schedule forecast vượt xa ESCALATE_COST_PCT=15% / ESCALATE_SCHEDULE_PCT=20% so với baseline (86-task feature chưa merge). → ESCALATE.
- **Rule 5** — Access model đổi qua gateway + đảo giả định phân quyền (C6) = breaking change tới bề mặt MCP mà client đang dùng. → ESCALATE.
- (Baseline platform/business còn TBD; theo `squad-baselines` đây vừa là **mở rộng baseline** cần CEO chốt tại Gate 1.)

### Khuyến nghị cho CEO (Gate 1)

**(a) Phân rã CHG-001 thành epic/phase triển khai TRÊN NỀN hiện tại (không tạo feature mới — theo quyết định CEO):**

| Epic | Nội dung | Dựa trên nền |
|---|---|---|
| E0 | **Đóng scope cũ** (9-nguồn read-only): close R-001..R-005, Gate C, merge | — (đã gần xong) |
| E1 | **Knowledge Store schema+**: `document_versions`, `entities`, `relationships`, `knowledge_summaries`, `document_permissions`, migrations | pgvector store + migration pipeline hiện có |
| E2 | **Jira** connector (ingestion) + Live MCP Jira | connector Confluence/GitLab, pipeline ingest/embedding |
| E3 | **Hybrid-RAG engine**: keyword + metadata + relationship + RRF + reranker + context-compression + context-pack | semantic search + pgvector hiện có |
| E4 | **Knowledge MCP** tool nghiệp vụ (search_company_knowledge, get_service, get_repository, search_code, get_jira_context, find_related_knowledge, get_knowledge_summary, get_document_version) | envelope/citation + tool surface hiện có |
| E5 | **Live vs Knowledge** decision + freshness + offline-source behavior + conflict/authority/hallucination control | provenance/trace đã có |
| E6 | **Permission enforcement server-side** (document_permissions, filter trước context assembly) — đảo C6 | ADR-0016 (kb team-only) là điểm bắt đầu |
| E7 | **Company MCP Gateway** (auth/authz/policy/audit/routing/rate-limit) — tầng cuối, chạm C3/C4 | transport-agnostic runtime (ADR-0002) |
| E8 | **Observability + evaluation** retrieval (Recall@K/MRR/NDCG, tool-call metrics) | SLIs/log hiện có |

**(b) Thứ tự khuyến nghị — ĐÓNG GATE C SCOPE CŨ TRƯỚC, rồi mở phase CHG-001 (KHÔNG gộp):**
1. Lý do: nền 9-nguồn đã có 1975 test pass, chỉ còn 5 review item + merge — gần tới mốc giá trị. Gộp CHG-001 vào trước go-live sẽ **đóng băng một deliverable gần xong** sau một khối việc rất lớn, kéo dài schedule baseline khó kiểm soát và trộn blast-radius.
2. E0 cho một mốc go-live sạch (hoặc ít nhất merge + Gate C) làm nền ổn định; CHG-001 (E1→E8) là một **plan baseline mới** CEO duyệt tại Gate 1, chạy như các phase kế tiếp trên cùng feature folder.
3. E1 (schema) và E2 (Jira) có thể bắt đầu song song ngay sau E0 vì tái dùng pipeline hiện có; Gateway (E7) để sau cùng vì chạm C3/C4 và cần permission (E6) trước.

**(c) Các điểm cần SA phân tích ≥3 options (ADR + options.md) trước khi build:**
- **Gateway build-vs-buy:** (1) tự viết FastAPI gateway; (2) MCP gateway/proxy có sẵn (OSS) + policy layer mỏng; (3) chưa làm gateway ở v1.1, giữ multi-server stdio + đẩy authz vào Knowledge MCP (hoãn C3 drift). Tiêu chí: auth/audit/rate-limit, lock-in, thời gian, giữ C4.
- **Reranker local-vs-API:** (1) cross-encoder local (bge-reranker) — khớp chính sách "dữ liệu không ra ngoài" như embedding bge-m3; (2) reranker API (Cohere/Voyage) — nhanh triển khai, nhưng data egress = vendor trả phí mới (deviation +40, cần CEO); (3) không reranker ở v1.1, chỉ RRF. Tiêu chí: chất lượng (NDCG), chi phí, egress, latency.
- **Relationship store trong Postgres vs graph riêng:** (1) bảng `entities`/`relationships` trong Postgres (spec mặc định, tái dùng store); (2) recursive CTE/`ltree`/Apache AGE extension trong Postgres; (3) graph DB riêng (Neo4j) — datastore mới + có thể vendor mới. Tiêu chí: độ sâu traversal cần cho find_related_knowledge, vận hành 1 store, deviation.
- (Phụ, SA cân nhắc) **Context compression:** rule-based/extractive vs LLM-based (thêm chi phí token + có thể egress).

**(d) Rủi ro / ADR mới cần viết:**
- **ADR-deviation (bắt buộc)** theo `squad-baselines`: baseline affected (platform+business), deviation score 100/100 itemised, invariant impact, in-baseline options, decision sought, cost/risk, recommendation.
- ADR Gateway (build-vs-buy, transport, authn/SSO) — chạm C3/C4/NFR-005.
- ADR Permission model server-side — **đảo C6/BR-003**; định nghĩa principal/permission mapping từ 3 nguồn; rủi ro: lộ tài liệu bị giới hạn nếu filter sai (hiện ADR-0016 chỉ team-only cho kb).
- ADR Hybrid-RAG retrieval (RRF weights, reranker, compression, context-pack schema) + eval harness.
- ADR Relationship/entity model (store + extraction trong ingest).
- ADR Document versioning & retention (snapshot retention: "biết gì tháng trước").
- ADR Jira connector + Live-vs-Knowledge decision + freshness/offline-source + conflict/source-authority (hallucination control).
- **Rủi ro xuyên suốt:** (i) read-only invariant C1 phải giữ nguyên qua gateway + Jira + Live MCP; (ii) permission filter là bề mặt bảo mật mới — HIGH nếu sai; (iii) reranker/compression/embedding egress phải khớp chính sách "dữ liệu nội bộ không ra ngoài"; (iv) schema mới migrate trên pgvector đang chạy — cần migration an toàn, không phá corpus hiện có; (v) token/cost cho reranker + compression + eval.

- **Checkers:** n/a (mode `blocked`, không phải promote/cab-readiness; santa-method không áp dụng).
- **Rationale:** CHG-001 vượt mọi ngưỡng deviation (score cap 100, nhiều hard-deviation đơn lẻ đã >10) và fire escalation rule 1/2/5; nó thêm Must-have mới và drift ra ngoài option đã duyệt — vượt quyền CTO, không thể route như stale re-run. CEO phải chốt scope/budget/milestone ở Gate 1 và quyết mở rộng baseline.
- **Conditions / follow-ups:** đóng Gate C scope cũ (E0) trước; SA viết ADR-deviation + options.md cho (c); DM amend `2-gate1/plan-approval.md` sau khi CEO duyệt; không build CHG-001 trước Gate 1.
- **Returned to:** none (ESCALATE lên CEO, không RETURN stage).
- **Escalation:** rule 1 (Must-have mới + drift ngoài option) + rule 2 (cost/schedule) + rule 5 (breaking change bề mặt MCP). Options cho CEO: **(A)** đóng Gate C scope cũ trước → mở CHG-001 làm plan-baseline mới theo epic E1..E8 *(khuyến nghị)*; **(B)** gộp CHG-001 vào trước go-live đầu tiên *(không khuyến nghị — đóng băng deliverable gần xong, schedule khó kiểm soát)*; **(C)** cắt phạm vi CHG-001 cho v1.1 (ví dụ hoãn Gateway E7) để giảm deviation/chi phí. Khuyến nghị CTO: **(A)**.

---

## D-002 · cab-readiness · 2026-10-01T18:30+07:00

(D3 — PRE exit → CAB; `ENVIRONMENTS=uat,pre,prod` nhưng kiến trúc không có service UAT/PRE dùng chung, nên D3 cũng phủ các exit criteria của UAT per `squad-env-promotion`.)

- **Decision: READY_FOR_CAB.**
- Tier: large (giữ nguyên — không re-size; không có full-track signal mới ngoài CHG-001 vốn đã escalate ở D-001)
- Evidence: `6-verify/review-report.md#Verdict`, `7-release/cab-pack.md#3`, `records/errors.md` (E-001..E-004), `4-design/architecture.md#Observability`, `api-contract.yaml` —
  - `6-verify/review-report.md` round 2 → **APPROVE**, 0 CRITICAL / 0 open HIGH; 12 MEDIUM + 10 LOW deferred.
  - `records/errors.md` → E-001…E-004 đều có record `· verified`; `scripts/squad/errors.sh open docs/squad/features/mcp-data-platform` → **no open S1/S2**.
  - `7-release/cab-pack.md` §3 evidence (dev regression 1846 passed/174 skipped/0 failed; full `e2e/` **34/34, 0 skipped**; 10 P1 pgvector E2E 10/10 trên PostgreSQL 16.15 + pgvector 0.8.6; BE coverage **89.97%** on changed code), §5 deployment (có backup-check trước migration), §6 rollback (**< 60s**, gỡ per-user stdio entry + stop scheduler), §7 observation window 30′ + SLI read-out mapping từng rollback trigger, §9/§9.3 residual risks (open S3/S4 = None), §9a baseline check (**0% scope reduction**, cost/schedule trong envelope Gate-1).
  - `scripts/verify_tool_surface.py` 42/42 + `scripts/validate_contract.py` OK (read-only surface audit thay cho production/server audit — không có hosted service để audit).
- **Checkers:** 2 independent checkers (santa-method, bắt buộc ở D3 trên mọi track).
  - Checker 1 (exit-criteria rubric squad-env-promotion PRE→CAB): review APPROVE ✔, no open S1/S2 ✔, 4/4 defect verified ✔, rollback plan + measured time ✔, triggers↦SLI ✔, observation window ✔, open S3/S4 none ✔, status READY_FOR_CAB ✔ → **PASS**.
  - Checker 2 (judgment items): every TC xanh trên PRE-equivalent (e2e 34/34) ✔, coverage 89.97% ≥ 80% ✔, security 0 open CRIT/HIGH ✔, read-only surface audit 42/42 ✔, migrations expand-only + backup-check ✔, baseline delta 0% ✔, no-shared-PRE substitution là sự thật kiến trúc đã duyệt Gate 1 ✔, risk=medium khớp matrix ✔; **NFR-003 chưa đo** → không phải fail tiêu chí mà là caveat phải CEO chấp nhận (xem Escalation) → **PASS với điều kiện caveat 1 lên CEO**.
  - Hai checker **đồng thuận PASS** ngay vòng 1; không bất đồng → không cần vòng 2, không fire rule 6.
- **Rationale (≤5 dòng):**
  1. Mọi PRE-exit criterion khách quan đều đạt với bằng chứng chỉ đúng commit `0363fcc`: review APPROVE 0/0 HIGH, error ledger sạch (0 S1/S2, 4/4 verified), e2e 34/34 trên pgvector thật, coverage 89.97%, read-only surface 42/42, baseline delta 0%.
  2. UAT/PRE "dùng chung" là **n/a theo thiết kế** (per-user local stdio, không service chung) — đây là option CEO đã duyệt ở Gate 1, không phải lược bỏ quy trình; PRE-equivalent chạy trên host dev thật + Docker. Rollback <60s là chi phí thật của bước gỡ per-user entry, không cần rehearsal vì không có service để drain.
  3. 5 Gate-C caveat + 22 MEDIUM/LOW deferred: **không mục nào reachable như lỗi an toàn/mất dữ liệu trên target v1 local** (cả 2 vòng review xác nhận); không mục nào phải đóng trước go-live. Read-only invariant (NFR-001) giữ nguyên.
  4. Lỗ hổng duy nhất là **NFR-003 (chất lượng truy hồi ngữ nghĩa) chưa đo** — bị chặn môi trường (HF egress 403) + phụ thuộc ADR-0010 chưa chốt; không sửa được trước CAB và không phải S1/S2 ⇒ đây là **chấp nhận rủi ro mức chất lượng của Must FR-013**, thuộc quyền CEO ở Gate 2, không phải quyền CTO đóng/bỏ qua một mình.
- **Conditions / follow-ups:**
  - **(ĐK1 — bắt buộc, cho CEO ở Gate 2):** CEO/PO **phải chấp nhận tường minh** rằng v1 ship với NFR-003 (chất lượng truy hồi ngữ nghĩa) **chưa được chứng minh** — con số recall ≥ 0.95 chỉ là ANN-index correctness dưới fake provider. Nếu CEO không chấp nhận, go-live phải hoãn tới khi unblock HF egress + chốt ADR-0010 + chạy `--provider configured`. (ghép với R-014/R-015.)
  - **(ĐK2 — hậu go-live, chặn CHG-001):** R-006/R-007 (migration-locking: thiếu `NOT VALID`, không `CREATE INDEX CONCURRENTLY` trong transaction) **phải sửa trước lần thay đổi schema kế tiếp trên dữ liệu prod đã có** — tức trước epic E1 của CHG-001. An toàn cho lần deploy đầu (empty table) mà thôi.
  - **(ĐK3):** R-013 — Redis dev ACL `mcp_ro` nới quyền + `default nopass +@all` **tuyệt đối không tái dùng cho staging/shared**; ghi vào env-promotion của CHG-001.
  - **(ĐK4):** R-005 secondary (`packages/conftest.py::_find_pg_bin()` Debian-only ⇒ 153 pg test skip trên non-Debian), R-012 (prune `--older-than` lệch contract `anyOf`, hướng an toàn), R-016 (CI chạy tay `make ci`, chưa có runner cưỡng chế) — follow-up chất lượng, không chặn go-live.
  - Smoke + observation window 30′ phải xanh sau cut-over (per cab-pack §7); mọi rollback trigger → rollback ngay rồi mở `incident` + Gate 2 mới.
- **Caveat tôi xác nhận đưa lên CEO ở Gate 2 (per cab-pack §9.0):**
  1. **NFR-003 semantic quality UNVERIFIED** — cần CEO/PO chấp nhận rủi ro tường minh (ĐK1).
  2. 153 package-level pg integration test skip trên host non-Debian (hành vi đã phủ qua e2e `MCP_E2E_PG_URL`).
  3. CI chưa được runner cưỡng chế (5 gate chạy `make ci` thủ công).
  4. Migration-locking chỉ an toàn cho lần deploy đầu (empty table).
  5. Live-source checks là checklist sign-off thủ công (cần VPN/credential thật).
- **Returned to:** none.
- **Escalation:** Không ESCALATE toàn-quyết-định (không rule nào ở `squad-decision-rights` fire để lật READY_FOR_CAB: NFR-003 không phải S1/S2 nên rule 4 không fire; cost/schedule trong envelope; không vendor/paid mới; không breaking-change contract đang dùng; checkers đồng thuận). **Nhưng** caveat 1 (NFR-003) là một quyết định chấp nhận rủi ro **thuộc CEO/PO ở Gate 2** — tôi chuyển lên như điều kiện duyệt CAB, kèm khuyến nghị: **chấp nhận cho v1** (surface read-only, không mất dữ liệu, phép đo bị chặn môi trường ngoài tầm feature; đo thật sẽ làm trong CHG-001 khi egress/ADR-0010 được giải quyết). Nếu CEO muốn chắc chắn trước, lựa chọn thay thế là hoãn go-live tới khi đo được NFR-003 — không khuyến nghị vì đóng băng một deliverable read-only an toàn gần xong.

---

## D-003 · retro · 2026-10-01T21:24+07:00

- Decision: ACK (blameless retro after successful local go-live; wrote `9-retro/retro.md`, appended L-001..L-003 to `knowledge/lessons.md`).
- Tier: large (unchanged — no re-size signal; CHG-001 already escalated at D-001)
- Evidence: `records/errors.md` E-001..E-004 (4/4 closed, root causes), `6-verify/review-report.md` R-001..R-027 (round 2 APPROVE), `records/decisions.md` D-001/D-002, `state.json` history, `7-release/release-log.md`.
- Rationale: Delivered to local go-live with 0 defects escaped to a live env, 0 rollbacks; the review caught all 5 HIGH before any live env. One recurring defect class (E-001/002/003: a safety guarantee asserted in prose, not enforced at a choke point, with no adversarial test) escaped qa-plan+qa-verify and seeds L-001. NFR-003 ships UNVERIFIED as a CEO-accepted residual risk (D-002/DK1), to be measured in CHG-001.
- Conditions / follow-ups (carry into CHG-001, before build): DK2 — fix R-006/R-007 migration-locking (`NOT VALID`, `CREATE INDEX CONCURRENTLY` outside the per-file transaction) before the next schema change on populated prod data (epic E1); DK3 — Redis dev ACL (`mcp_ro` broad grants + `default nopass +@all`, R-013) never reused for staging/shared, record in CHG-001 env-promotion; measure NFR-003 for real (clear HF egress / VPN + finalise ADR-0010 model, run `--provider configured`) — this also unblocks the S2 embedding bake-off; make `packages/conftest.py::_find_pg_bin()` portable (honour `PATH`/env override) so pg integration tests run off Debian; set `TOKEN_BUDGET_M` and wire `ecc:cost-tracking` so the next retro can report per-stage spend.
- Returned to: none.
- Lessons added: L-001 (all), L-002 (squad-sa), L-003 (all).
- Distill: NOT due — `knowledge.sh due` = 0/3 finished features since last distill, 0/40 active lessons. No distill this turn.
