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


---

## CHG-002 — "AI hiểu công ty": 10 building blocks + Orchestrator/Harness (enhancement, phased)

- **Recorded:** 2026-10-01T22:10+07:00 by Delivery Manager
- **Requested by:** CEO
- **Kind:** Kind 2 — enhancement layered on top of CHG-001 (Option C approved in the same message)
- **CEO intent:** mục tiêu "AI càng dùng lâu càng hiểu công ty nhưng KHÔNG mơ hồ/suy diễn thành fact" — không
  thiết kế chỉ quanh MCP + RAG. CEO đã tự nghiên cứu một mô hình, muốn squad điều tra theo hướng đó, đánh giá
  tính khả thi từng phần, và chia phase triển khai.

### Mô hình CEO đề xuất (10 building blocks + Orchestrator/Harness)
1. Ingestion — pipeline nhiều nguồn (GitHub/Jira/Confluence/PDF/Markdown/DB/API specs/Slack) → normalize → chunk → store.
2. Knowledge Model — Document Store → Company Knowledge Graph (Company ├ Product/Service/Team/API/Database/Event/Business Rule/ADR/Person-Owner). CEO coi đây là đầu tư đáng giá nhất.
3. RAG/Retrieval — keyword + vector + metadata + knowledge graph + reranker; lọc entity/type/status/env trước semantic.
4. Grounding/Evidence — không evidence → không phải company fact; mỗi claim có Source/version/Owner/Updated/Confidence/Evidence; thiếu nguồn → "UNKNOWN", không bịa.
5. Memory — khác RAG: AI đã học/quyết định gì ở các lần trước (Decision/Date/Reason/Status); có TTL/version/status.
6. State — AI đang làm gì, tới đâu (task progress). Memory != State.
7. Provenance — info đến từ đâu + đã đổi thế nào (fact <- doc version <- ADR <- approver <- date); doc v2->v3 => fact cũ obsolete.
8. Governance — Official/Draft/Deprecated/Experimental/Unknown; câu hỏi "company fact" chỉ dùng Official + Active.
9. Evaluation — test AI như test software: ~100 Company Questions (Q->expected), run eval mỗi lần đổi RAG; giảm chất lượng → không deploy.
10. Observability — trace question->retrieval->docs->rerank->context->LLM->MCP->answer; đo latency/token/MCP calls/docs/grounding-failures/cost.
- Orchestrator + Harness bọc ngoài Core; MCP là access layer xuống GitHub/DB/Jira/S3/API.

### CEO roadmap (4 phase)
- P1: Ingestion -> Store -> RAG -> MCP (≈ nền hiện tại + CHG-001).
- P2: Knowledge Model -> Metadata -> Provenance -> Grounding.
- P3: Memory -> State -> Orchestrator -> Harness.
- P4: Evaluation -> Observability -> Governance.

### Quan hệ với trạng thái hiện tại (DM sơ bộ — researcher/SA xác nhận)
- Đã có / sắp có: Ingestion (mcp_ingest, live), RAG cơ bản (pgvector), MCP (9 server); CHG-001 Option C thêm
  Hybrid-RAG, versioning (~Provenance một phần), entities/relationships (~Knowledge Model một phần), permission
  server-side (~Governance một phần), eval/telemetry E8 (~Evaluation/Observability một phần).
- Enhance LỚN chưa có trong CHG-001: Knowledge Model đầy đủ (graph entity-type phong phú hơn CTE 2-3 hop),
  Grounding/Evidence contract (fact-or-UNKNOWN + confidence), Memory (quyết định học được, TTL/status), State
  (task progress), Provenance đầy đủ (chain + obsolete propagation), Governance lifecycle, Orchestrator + Harness.
- Rủi ro: CHG-002 chồng lấn CHG-001 ở nhiều block → researcher phải ánh xạ rõ để không làm hai lần.
  Memory/State/Orchestrator có TRẠNG THÁI GHI → kiểm tra xem có phá bất biến read-only không (ghi store nội bộ
  != ghi vào nguồn); vẫn phải giữ stdio NFR-005 + grounding "không evidence → không fact" (khớp L-001/L-002).

### Status
- [x] CHG-001 Option C approved (2-gate1/plan-approval.md).
- [x] Researcher điều tra tính khả thi CHG-002 (1-discovery/market-research-chg002.md): mapping 10 block, top giá
      trị B4 Grounding, 3 cảnh báo bất biến (read-only giữ được qua kb.agent_* + credential riêng; Orchestrator
      phải in-process giữ stdio; LLM-server = egress/vendor cần CEO), phasing P2a→P4.
- [x] **CEO scope decision (2026-10-01T22:30): triển khai NGAY B1 + B3 (đã có) + B4 Grounding contract. Phần
      còn lại (B2 Knowledge Model, B5 Memory, B6 State, B7 Provenance, B8 Governance, B9 Evaluation, B10
      Observability, Orchestrator/Harness) → BACKLOG; CEO sẽ hỏi lại để phát triển sau.**
- [ ] SA ra ADR + (nếu cần) options cho B4 Grounding contract → CHG-002 Gate 1 (nhỏ, scope hẹp).
- [ ] Build B4 sau CHG-001 (B4 enforce trên context-pack của CHG-001 → cần CHG-001 có trước hoặc song song).

### Backlog (CHG-002 deferred — CEO sẽ hỏi lại sau)
- B2 Knowledge Model đầy đủ (ontology + entity extraction) — khó nhất, có thể cần LLM.
- B5 Memory (store ghi nội bộ, TTL/status) · B6 State (task progress) — cần kiểm bất biến read-only.
- B7 Provenance đầy đủ (chain + obsolete propagation v2→v3) — bắt buộc sau B2.
- B8 Governance lifecycle (Official/Draft/Deprecated/Experimental/Unknown).
- B9 Evaluation (100 Company Questions + deploy-gate) · B10 Observability (trace end-to-end + grounding-failure).
- Orchestrator + Harness — ESCALATE: quyết LLM-ở-đâu (local offline / không-gọi-LLM-server / API-deviation).

### B4 Grounding contract — scope triển khai ngay (CEO)
Mục tiêu: "không evidence → không phải company fact". Mỗi claim có Source / source-version / Owner / Updated /
Confidence / Evidence; thiếu nguồn chính thức → trả **UNKNOWN** ("Tôi không tìm thấy nguồn chính thức xác nhận"),
KHÔNG bịa. Enforce tại MỘT choke point trên context-pack (khớp L-001 choke point + adversarial test, L-002 không
khẳng định sai). Không cần LLM server, không egress → an toàn trong baseline hiện tại (vendors=none, stdio, read-only).
B4 làm trên context-pack của CHG-001 nên gắn với CHG-001 (sau/song song).


---

## D-004 · blocked · 2026-10-01T22:40+07:00
- Decision: APPROVE — xác nhận kết luận của SA: B4 Grounding/Evidence contract **nằm TRONG scope deviation CHG-001 (Option C, ADR-0017) mà CEO đã duyệt Gate 1** → **KHÔNG cần Gate CEO mới**; CTO quyết trong scope. ADR-0018 giữ **proposed** (chưa accepted) tới khi design/eval chốt ngưỡng confidence.
- Tier: large (không đổi — CHG-001 đã escalate ở D-001; B4 không phải tín hiệu re-size riêng, nó là một contract hẹp siết lại các mảnh đã có).
- Evidence: `docs/adr/0018-grounding-evidence-contract.md` §Decision 1–7 + §"CEO note / deviation"; `docs/adr/0017-chg001-company-knowledge-deviation.md` §"Decision sought" (Option C) + §"Invariant impact"; `2-gate1/plan-approval.md` (CEO APPROVED Option C: context-pack + permission server-side, keep stdio, vendors=none, reranker local offline); `records/decisions.md` CHG-002 §"CEO scope decision 22:30" (B4 triển khai ngay) + §"B4 Grounding contract — scope"; `knowledge/lessons.md` L-001, L-002; `.kiro/squad/config.env` (DEVIATION_THRESHOLD_PCT=10, ESCALATE_COST_PCT=15, ESCALATE_SCHEDULE_PCT=20, ENVIRONMENTS=uat,pre,prod); `squad-decision-rights` §"must escalate".

### Trả lời bốn câu hỏi

1. **B4 in-scope CHG-001 (yes) — không phải deviation mới, không escalate CEO.** Kiểm từng cổng escalate của `squad-decision-rights` so với baseline (plan-approval.md Option C):
   - Rule 1 (Must drift / Must mới / rời option đã duyệt): KHÔNG. B4 enforce "không-evidence ⇒ UNKNOWN" tại context-pack assembler — đúng tầng CHG-001 Option C đã duyệt (context-pack + permission server-side + một choke point L-001). Nó **siết** các mảnh đã có (ADR-0004 envelope/citation, spec §39–§42, §13–14), không thêm năng lực rời option.
   - Rule 3 (vendor/paid service/egress mới): KHÔNG — thứ duy nhất có thể đẩy CHG-001 vượt baseline (và cộng +40 deviation theo ADR-0017) là mở reranker/compression **API trả tiền**. ADR-0018 §7 khẳng định tường minh: **không vendor/egress/service/datastore mới**, verdict + confidence **deterministic**, reranker/embedding local offline (`HF_HUB_OFFLINE=1`). Không chạm cổng này.
   - Rule 5 (breaking contract đang dùng): KHÔNG — mở rộng envelope ADR-0004 **tương thích ngược** (thêm `provenance` + `grounding` per-claim, thêm status `insufficient_evidence` nối tiếp tinh thần `empty`/`not_found`); không phá bề mặt client đang đăng ký.
   - Invariant: B4 **giữ** read-only (ADR-0003), stdio (ADR-0002/NFR-005), permission server-side **trước** gate (ADR-0016, §24/§43). KHÔNG mở invariant mới ngoài những gì ADR-0017 đã mở và CEO đã amend ở plan-approval.md (C6/BR-003). Deviation của B4 = **con của CHG-001**, không phải deviation mới ⇒ không kích `DEVIATION_THRESHOLD_PCT`.
   → Không cổng nào fire. Decision rights: **CTO quyết alone (D1/design-level)**, không ESCALATE.

2. **Confidence threshold để TBD cho design/eval — CHẤP NHẬN.** Khớp L-002/E-004: ngưỡng FACT↔LOW_CONFIDENCE chỉ có nghĩa khi đo trên **golden-set công ty thật**, mà corpus thật + embedding đo được còn **chặn bởi egress HF (NFR-003 UNVERIFIED — D-002/DK1)**. Chốt một số cứng bây giờ = đúng lỗi "số không đo được bị đọc như bằng chứng". Điều kiện giữ an toàn: bất biến **"không evidence hợp lệ ⇒ UNKNOWN, bất kể confidence"** (ADR-0018 §3) độc lập ngưỡng và enforce được NGAY; confidence phải mang nhãn `confidence_basis = evidence-strength, NOT P(claim true)` và không gate nào đọc nó thành độ-đúng (GT-6). TBD chỉ áp cho ranh giới FACT↔LOW_CONFIDENCE, phải chốt bằng dữ liệu đo + ghi lại trong design/eval; không được khẳng định ngưỡng cứng tới lúc đó.

3. **Hướng build B4: SAU / song-song CHG-001, bám context-pack assembler (epic E3) là choke point chuẩn.** Choke point chuẩn = context-pack assembler (sau rerank + compression, ngay trước khi trả client) — module này thuộc CHG-001 E3, **đã duyệt Gate 1 nhưng chưa viết**. Quyết:
   - **Ưu tiên:** B4 giao sau/song-song để gate cắm thẳng vào context-pack assembler → một choke point duy nhất ngay từ đầu, không phải dời.
   - **Fallback (chấp nhận):** nếu B4 đến trước module E3, cho phép cắm **tạm** vào ranh giới trả kết quả tool semantic-search (`mcp_pgvector` → mapper envelope ADR-0004) như ADR-0018 §2, với **ba điều kiện bắt buộc**: (a) implementation-plan ghi rõ việc **DỜI** gate vào assembler khi E3 ra đời; (b) **tuyệt đối không để hai gate song song** — khi dời xong, gate tạm bị gỡ (GT-5 "single choke point" assert điều này); (c) ở trạng thái tạm, confidence chỉ dùng thành phần retrieval (chưa có rerank/RRF/compression) nhưng verdict FACT/UNKNOWN/CONFLICT vẫn đúng ngữ nghĩa. Việc dời + GT-5 là điều kiện Done của B4, không phải tuỳ chọn.

4. **ADR-0018: GIỮ proposed, CHƯA accepted — chờ design/eval.** Lý do: §Decision 3 (công thức + ngưỡng confidence) còn `TBD cho design/eval`; đúng theo chính dòng Status của ADR ("đưa accepted khi design/eval chốt các ngưỡng còn TBD"). CTO xác nhận **nội dung + hướng** của ADR-0018 là đúng và được build theo (contract machine-checkable, server-side, một choke point, GT-1..GT-7, bất biến §7); nhưng promotion proposed→accepted để lại cho mode `design` khi SA/BA/QA chốt công thức + ngưỡng và viết `api-contract.yaml` hình dạng envelope. SA cập nhật Status khi đó.

- Checkers: không áp dụng (mode `blocked`, không phải promote/cab-readiness; santa-method không bắt buộc).
- Rationale: B4 là một contract hẹp siết các mảnh đã có trong Option C đã duyệt; không thêm vendor/egress/service/datastore, giữ read-only/stdio/vendors=none, mở rộng envelope ADR-0004 tương thích. Không cổng escalate nào fire ⇒ CTO quyết trong scope, không cần Gate CEO mới. TBD ngưỡng là trung thực theo L-002; bất biến "không-evidence⇒UNKNOWN" đã chặn bịa ngay. Build bám choke point chuẩn (context-pack assembler), fallback tạm có điều kiện dời + GT-5 chống hai-gate.
- Conditions / follow-ups:
  - (C1) ADR-0018 chỉ accepted ở mode `design` sau khi chốt công thức + ngưỡng confidence trên dữ liệu đo; tới đó giữ proposed.
  - (C2) Design/eval phải chốt confidence formula + thresholds trên golden-set thật; **gate "chất lượng giảm → không deploy"** áp cho B4 (nối B9/NFR-003). Trước khi đo thật được, ngưỡng FACT↔LOW_CONFIDENCE là UNVERIFIED, ghi cạnh NFR-003 (L-002).
  - (C3) Nếu dùng fallback choke point tạm: implementation-plan ghi việc dời vào assembler + GT-5 assert một-chỗ-duy-nhất; không hai gate song song.
  - (C4) QA viết GT-1..GT-7 là contract test ở mode design/plan (cùng cấp E-001..E-003 của L-001); các assert FACT/UNKNOWN/CONFLICT chạy ngay, không chờ ngưỡng.
  - (C5) NFR-003 vẫn UNVERIFIED (egress HF) được mang như residual risk đã CEO-accept (D-002/DK1); B4 không tự khẳng định đo được recall thật.
  - (C6) Nếu design phát hiện B4 cần một phụ thuộc mới (datastore/extension/service/vendor/egress) → QUAY LẠI ADR riêng + CTO; nếu là vendor/egress/paid-service thì khi đó Rule 3 fire ⇒ ESCALATE CEO. Hiện tại không có.
- Returned to: squad-sa (owning stage của ADR-0018 + B4 design) — không phải RETURN vì defect, mà là route tiếp: tiến vào mode `design` của B4 với các điều kiện C1–C6; ADR-0018 giữ proposed.
- Escalation: none — không cổng `squad-decision-rights` nào fire; B4 nằm trong deviation CHG-001 Option C đã duyệt Gate 1.

---

## D-005 · plan-review · 2026-10-01T23:40+07:00

(CHG-001 Option C + B4 — design + plan trước khi backend build; chốt baseline schedule; xác nhận ADR promotions)

- **Decision: APPROVE** (cho qa-plan + backend build CHG-001, T-087..T-110). Baseline cost/schedule CHG-001 **đã chốt** ở quyết định này (xem §Baseline).
- Tier: large (không đổi — CHG-001 đã escalate ở D-001; không có tín hiệu re-size mới. CHG-002 B2/B5..B10/ORCH vẫn ngoài scope, chỉ B4 in-scope per D-004).
- Evidence: `4-design/architecture.md#Company-Knowledge-tier`, `4-design/api-contract.yaml` (13 operationId x-change CHG-001 + GroundedResultBase), `3-spec/requirements.md` (FR-016..FR-022, GT-1..GT-7), `5-plan/implementation-plan.md` (T-087..T-110), `docs/adr/0017-chg001-company-knowledge-deviation.md`..`0022-knowledge-domains-cte-migration-locking.md`, D-001/D-002/D-004, L-001/L-002/L-003 — chi tiết:
  - `4-design/architecture.md` "Company Knowledge tier — CHG-001 Option C + B4", "Epic map E1..E8", "CHG-001 — `search_company_knowledge` (grounded, 2 choke point)" key-flow, "Company Knowledge domains (ADR-0022) — migrations 0007/0007b/0008 + luật DK2", "Observability → Company Knowledge SLIs", "Security & threat model" (R20/R21/R22), "self-review CHG-001".
  - `4-design/api-contract.yaml` — 13 operationId mới (8 Knowledge + 5 Jira) đều hiện diện, `x-change: CHG-001`, `GroundedResultBase` + `status=insufficient_evidence` (xác minh grep 56 match).
  - `3-spec/requirements.md` — FR-016..FR-022 (26 AC mới), NFR-006..NFR-012, EB-001..EB-005 regression, BR-006..BR-012, bảng FR→tool/ADR + GT-1..GT-7 → FR-021 AC map.
  - `5-plan/implementation-plan.md` CHG-001 — T-087..T-110 (24 task), epic→task map, coverage matrix (26/26 AC phủ, `uncovered_ac` rỗng), parallelism + critical path, "Milestones vs baseline", "Release path", DoD 11–14, Risks R-C1..R-C9, "Ghi chú cho qa-plan".
  - `docs/adr/0017..0022` Status: 0017 **accepted** (CEO Gate 1), 0019/0021/0022 **accepted**, 0018 **proposed**, 0020 **proposed** (xác minh grep).
  - `records/decisions.md` D-001 (escalate/Option A→C), D-002 ĐK2/ĐK3, D-004 (B4 in-scope, C1–C6), `records/backlog.md` (CHG-002 out-of-scope), `knowledge/lessons.md` L-001/L-002/L-003.
  - `2-gate1/plan-approval.md` (Option C đã duyệt; baseline cost/schedule CHG-001 **chưa chốt** — chờ options SA, nay CTO chốt).
  - `.kiro/squad/config.env` (DEVIATION_THRESHOLD_PCT=10, ESCALATE_COST_PCT=15, ESCALATE_SCHEDULE_PCT=20, ENVIRONMENTS=uat,pre,prod).

### 1. Nhất quán + đủ để build (design ↔ contract ↔ FR/AC ↔ plan)

| Kiểm | Kết quả |
|---|---|
| 13 tool mới (8 Knowledge + 5 Jira) trong architecture ↔ api-contract.yaml ↔ FR→tool table | **Khớp.** 13 operationId `x-change: CHG-001` hiện diện; envelope `GroundedResultBase` tương thích ngược ADR-0004 + `status=insufficient_evidence`. |
| FR-016..022 (26 AC) ↔ coverage matrix plan | **26/26 AC phủ**, `uncovered_ac` rỗng; mỗi tool mới ánh xạ ≥1 FR. |
| FR-021 ↔ GT-1..GT-7 | **Khớp** cả ở requirements (GT→AC map) và plan (T-107 contract test, "ghi chú qa-plan"). GT chạy ngay, độc lập ngưỡng τ. |
| Epic map E1..E8 ↔ T-087..T-110 | **Khớp 1-1**; epic→task map + dependency nhất quán giữa architecture và plan. |
| **Choke point #1 (permission) TRƯỚC #2 (grounding), không hai gate** | **Giữ.** E6 (T-104) chặn E8 (T-106) trong cả arch (key-flow + "E8 phải sau E6") và plan (critical path, blocking_tasks); GT-5 structural single-gate; L-001 honored; không dùng fallback D-004 §3 (assembler E3 xây trước E8). |
| **Read-only (0 write tool)** | **Giữ.** BR-006/NFR-006; 13 tool `x-readonly`/`x-side-effects: none`; Jira 0 create/transition/comment (T-094 test âm); startup read-only check (T-091/T-099); unknown-write-tool ở tầng JSON-RPC. |
| **stdio in-process, no network port** | **Giữ.** Gateway in-process (ADR-0021, NFR-012); T-109 test 0 listening socket + audit chỉ stderr. |
| **no-egress (HF_HUB_OFFLINE)** | **Giữ.** NFR-011; reranker/embedding local offline (T-097); test 0 outbound socket; confidence deterministic (không LLM/API). |
| **DK2 migration-locking là task đầu tiên** | **Giữ.** T-087 (fix runner: `NOT VALID`+`VALIDATE` riêng, `CREATE INDEX CONCURRENTLY` ngoài txn, `lock_timeout`) là task tuyệt đối đầu tiên, chặn T-088 và qua đó mọi epic — khớp ĐK2 (D-002) / DK2 (D-003), vì schema chạy trên pgvector đã go-live. DK3 (Redis ACL không tái dùng) ở T-089. |

Kết luận mục 1: design + plan **nhất quán và đủ để build**. Không phát hiện mâu thuẫn chặn.

### 2. Baseline schedule CHG-001 — CHỐT

Baseline cost/schedule CHG-001 **absent** ở Gate 1 (plan-approval.md/D-001 escalate; không có go-live date đã duyệt). Chốt tại đây để `schedule_delta_pct` về sau có mốc so sánh:
- **Baseline schedule (effort) = 70 agent-h** critical path (tận dụng song song E2∥E3, E5∥E7) / **89 agent-h** không song song. Lấy từ estimate per-task high-end của lead (T-087..T-110). Mốc so sánh về sau dùng **critical-path 70 agent-h** làm baseline chính; 89 agent-h là trần không-song-song.
- **Baseline cost = run ≈ $0/tháng** (vendors=none giữ nguyên: gateway code in-process, reranker `bge-reranker-v2-m3` local offline, Hybrid-RAG + 4 domain + Jira đều trong Postgres hiện có; chi phí thật = RAM/CPU host + thời gian vận hành). Build nội bộ, không vendor trả phí mới.
- **Escalate khi vượt:** `schedule_delta_pct > ESCALATE_SCHEDULE_PCT (20%)` so với 70 agent-h → ESCALATE; `cost_delta_pct > ESCALATE_COST_PCT (15%)` hoặc **bất kỳ** vendor/egress/paid-service/datastore mới (ví dụ reranker API) → ESCALATE (Rule 3). Nếu build phát hiện cần phụ thuộc mới → QUAY LẠI ADR + CTO, không âm thầm (D-004 C6).
- Từ nay `schedule_delta_pct` tính so **70 agent-h** (không còn "n/a baseline absent").

### 3. ADR promotions

Xác nhận trạng thái đúng, **không promote thêm**:
- ADR-0017 **accepted** (CEO Gate 1, Option C) ✓; 0019 (Jira) / 0021 (gateway in-process) / 0022 (4 domain + CTE + DK2) **accepted** ✓ — đúng với Option C đã duyệt.
- **ADR-0018 (B4 grounding) giữ `proposed`** — đúng: §Decision 3 (công thức + ngưỡng confidence FACT↔LOW) còn TBD; promote accepted ở mode `design`/eval khi chốt τ trên golden-set thật (D-004 C1). **Xác nhận đúng.**
- **ADR-0020 (Hybrid-RAG + reranker) giữ `proposed`** — đúng: chất lượng (NDCG/recall) chặn bởi egress HF (NFR-003 UNVERIFIED); promote khi đo được. **Xác nhận đúng.**
- Không có ADR proposed nào "sẵn sàng promote" bị bỏ sót: cả 0018 và 0020 đều phụ thuộc dữ liệu đo bị chặn egress → giữ proposed là trung thực (L-002).

### 4. Residual / điều kiện mang theo (không chặn APPROVE)

- NFR-003 recall thật + NFR-010 τ_fact/τ_low **UNVERIFIED** (egress HF) — mang như residual risk đã CEO-accept (nối D-002 ĐK1); bất biến no-evidence⇒UNKNOWN / CONFLICT enforce ngay (GT-1..GT-4, độc lập ngưỡng). Không bịa số; `calibration_status=uncalibrated`; comment `# THRESHOLD TBD` grep được (T-108).
- DK2 (T-087) + DK3 (T-089) là điều kiện bắt buộc trong plan — đã có task, không chặn.
- Open questions 6/7/8 (τ, latency budget, source-authority taxonomy) non-blocking, configurable/seeded.

- **Checkers:** n/a (mode `plan-review` — santa-method chỉ bắt buộc ở `promote`/`cab-readiness`).
- **Rationale (≤5 dòng):**
  1. Chuỗi design→contract→FR/AC→plan **nhất quán**: 13 tool đủ, 26/26 AC phủ, FR-021↔GT-1..GT-7 khớp, epic E1..E8 ↔ T-087..110 1-1.
  2. Bốn bất biến an toàn **giữ nguyên** và có task/test tương ứng: 2 choke point đúng thứ tự (perm trước grounding, không hai gate), read-only 0 write, stdio in-process 0 port, no-egress HF_HUB_OFFLINE; DK2 migration-locking là task đầu tiên chặn mọi epic.
  3. ADR promotions đúng: Option C-ADR đã accepted; 0018/0020 giữ proposed vì phụ thuộc dữ liệu đo bị chặn egress — trung thực theo L-002.
  4. Baseline CHG-001 (absent ở Gate 1) được chốt = 70 agent-h critical path (89 không song song), run ≈$0/vendors=none, để `schedule_delta_pct` có mốc.
  5. Không cổng escalate nào fire (cost/schedule trong envelope đã chốt; không vendor/egress/breaking-contract mới) ⇒ APPROVE, không ESCALATE.
- **Conditions / follow-ups:**
  - (C1) `schedule_delta_pct`/`cost_delta_pct` từ nay so với **70 agent-h / run $0**; vượt 20%/15% hoặc bất kỳ vendor/egress/paid-service/datastore mới → ESCALATE (Rule 3) hoặc quay lại ADR + CTO (D-004 C6).
  - (C2) T-087 (DK2 migration-locking) phải xanh **trước** mọi migration 0007+ và trước mọi epic khác; T-089 ghi DK3 (Redis ACL least-privilege per-env) vào env-promotion CHG-001.
  - (C3) ADR-0018 + ADR-0020 chỉ promote `accepted` ở mode `design`/eval sau khi gỡ egress HF + chốt τ/chất lượng trên golden-set thật (nối NFR-003/NFR-010/D-002 ĐK1); tới đó envelope `calibration_status=uncalibrated`, không khẳng định recall thật.
  - (C4) qa-plan viết GT-1..GT-7 + adversarial permission (FR-019 AC-002) + single-choke-point structural (FR-019 AC-003 + FR-021 AC-006 GT-5) ở cùng cấp E-001..E-003 của L-001; FR-014 AC-002 assert ở tầng JSON-RPC cho 2 server mới.
  - (C5) CHG-002 B2/B5..B10/ORCH giữ ngoài scope (backlog) — plan không chứa task nào cho chúng; chỉ B4 (E8). Giữ nguyên.
- **Returned to:** none (APPROVE). Flow tiếp: qa-plan (GT + adversarial contract tests) + backend build theo T-087..T-110, bắt đầu T-087.
- **Escalation:** none.

---

## CHG-003 — Stand up REAL ingestion + open REAL egress, CLI-driven, with a 9-source runbook (Confluence first)

- **Recorded:** 2026-10-02T10:05+07:00 by Delivery Manager / sized by CTO
- **Requested by:** CEO
- **Kind:** Kind 2 — change to a feature already in progress
- **Context:** CEO HOÃN go-live CHG-001 (no `cab-approval.md` written for CHG-001; see state 2026-10-02T10:00 `chg001-gate2 postponed`). Instead the CEO wants to make the system **real**: stand up live ingestion and open real egress to the actual sources, driven from the CLI the CEO runs locally, with a step-by-step runbook to integrate **all 9 sources, Confluence FIRST**.
- **Confirmed target (CEO):** Confluence **Cloud** at `https://tnexwm.atlassian.net` (flavor=Cloud — matches the locked baseline `confluence_flavor=Cloud` at Gate B).

### What this changes vs the locked baseline

Everything built so far (base 9-source + CHG-001) was validated under **vendors=none + no-egress + read-only + stdio NFR-005**, with real sources deliberately stubbed (demo HTTP stub for Confluence, DeterministicFakeProvider for embeddings, HF egress 403). CHG-003 is the step that **removes the stub**: it opens outbound network to a named external SaaS (`*.atlassian.net`) and introduces a real external vendor (Atlassian) plus a stored credential (API token + email). That is exactly the gate CHG-001/CHG-002 kept closed.

### Status
- [x] CTO (mode `blocked`) sized CHG-003 → **D-006** below: deviation ≥ cost cap, escalation rules **1 + 3** fire → **ESCALATE to CEO Gate 1 (small/scoped)**.
- [ ] DM escalate the egress/vendor/credential decision to CEO Gate 1 (lean track).
- [ ] CHG-001 stays **postponed** (artifacts preserved; measuring NFR-003 becomes *possible* once egress + a real embedding model are available — link to DK1).

---

## D-006 · blocked · 2026-10-02T10:08+07:00

(SIZE change CHG-003 — real ingestion + real egress, CLI runbook, Confluence first)

- **Decision: ESCALATE (→ CEO Gate 1, scoped/lean).** Deviation verdict: **vendor + egress = above threshold**; this is a Gate-1 matter, **NOT** a CTO-alone call.
- Tier: large (unchanged — same feature; CHG-003 is a focused change, not a re-size signal).
- Evidence: `2-gate1/plan-approval.md` + `4-design/architecture.md` #1022/#1138 + `records/decisions.md` D-001/D-004/D-005 + `knowledge/platform-baseline.md` + L-001/L-002 + `.kiro/squad/config.env` (DEVIATION_THRESHOLD_PCT) + `connectors/registry.py` — detail:
  - `2-gate1/plan-approval.md` (CHG-001 Option C: **vendors=none kept**, **no egress/service HTTP**, read-only, stdio NFR-005; `confluence_flavor=Cloud`).
  - `records/decisions.md` D-001 (deviation framework), D-004 §Rule-3 (reranker-API would be the one thing that fires Rule 3 / adds +40), D-005 §2 (baseline cost = run ≈ $0, vendors=none; "any new vendor/egress/paid-service → ESCALATE").
  - `4-design/architecture.md#271,#1022,#1138-1139` (vendors=none + no-egress itemised as a kept invariant; `HF_HUB_OFFLINE=1`), `#1177 R1` (VPN/network to real sources already observed unreachable), `#911` (source credential threat model: secret via env/`*_FILE`, never stdout, scrub() both ways — R-003/E-003 fix, L-001).
  - `knowledge/platform-baseline.md` (template-TBD except **Approved vendors = none**; so the effective baseline is plan-approval.md per `squad-baselines` bootstrapping).
  - `knowledge/lessons.md` L-001 (safety guarantee at one choke point + adversarial test), L-002 (don't read an unmeasured proxy as proof).
  - Code on disk: every package has a `doctor` subcommand (`serve|doctor|tools-dump`); `mcp-ingest` CLI = `run|status|reembed|prune` (+ `db status`); ingest **connectors exist for only 4 sources** — `connectors/registry.py` = {confluence, gitlab, opensearch, jira}. CloudWatch/Kibana/Kafka/Redis/SQS-SNS are **Live-MCP read-only only** (doctor+serve, no corpus ingestion).
  - `.kiro/squad/config.env`: DEVIATION_THRESHOLD_PCT=10, ESCALATE_COST_PCT=15, ESCALATE_SCHEDULE_PCT=20, DEPLOY_MODE=script, ENVIRONMENTS=uat,pre,prod.

### 1. Deviation score vs the approved plan baseline — invariants broken

Baseline measured against = `2-gate1/plan-approval.md` (CHG-001 Option C), the CEO-approved live baseline. Platform-baseline.md is still template-TBD, so per `squad-baselines` bootstrapping the Gate-1 approval is authoritative; it explicitly locks **vendors=none** and **no egress**.

| Deviation (hard) | Points | Invariant broken |
|---|---|---|
| **Data leaving the company boundary (new egress)** | 40 | **NFR-005-adjacent no-egress invariant** (plan-approval.md "vendors=none kept", architecture #1022/#1138 "không egress"). Real ingestion pulls from `https://tnexwm.atlassian.net` over the public internet → outbound egress to `*.atlassian.net`. This is the exact invariant CHG-001/CHG-002/D-004/D-005 kept closed. |
| **New external vendor / service outside the baseline** | 40 | **vendors=none** (platform-baseline "Approved vendors: none"; plan-approval.md). Atlassian Cloud becomes a real external dependency the running system reaches + authenticates to, with a stored API token. |
| **Soft (CTO judgement)** | +6 | High reuse (the Confluence connector, ingest pipeline, pgvector store, read-only + stdio all stay exactly as built — nothing is re-architected); the change is a configuration/operations boundary flip, not a code redesign. Low architectural drift keeps soft points modest. |
| **Total (cap 100)** | **86 (capped reporting ~86; two 40-point hard deviations each already ≫ 10)** | **≫ DEVIATION_THRESHOLD_PCT = 10.** |

**Invariants explicitly broken by CHG-003:** (a) **no-egress** — opened to `*.atlassian.net`; (b) **vendors=none** — Atlassian added as a real vendor + credential. **Invariants explicitly KEPT (must stay kept, and are a condition of my recommendation):** read-only-to-source (the 9 MCP servers + Jira stay read-only; egress is for the *ingest* pull only, which already runs under `mcp_ingest_rw` writing to `kb.*`, never writing to the source), stdio NFR-005 (no new network port opened by any MCP server), and the grounding/permission choke points from CHG-001.

> Note: this does NOT touch the HF-egress / NFR-003 question. Atlassian egress ≠ huggingface.co egress. Opening Atlassian lets ingestion store *real* Confluence content, but embeddings still use the fake provider unless HF egress + a real model (ADR-0010) are also opened — a **separable** decision. Keep it separable (see §3d).

### 2. Escalation rules fired (`squad-decision-rights` §must escalate)

- **Rule 3 — FIRES (decisive):** "A new paid external service, a new vendor, or **data leaving the company boundary not covered by the approved option**." CHG-003 opens egress to a named external SaaS and stores an API token. The approved option (Option C) explicitly says vendors=none / no egress. → **ESCALATE.** This confirms (not refutes) the CEO's framing: opening egress + storing a token is a **CEO Gate-1 matter, not a CTO-alone decision.**
- **Rule 1 — FIRES:** the work "drifts outside the approved option." Option C was approved on the premise of no egress; making ingestion real changes the premise. Also the CEO's go-live sequencing changed (CHG-001 postponed in favour of real integration) — a scope/sequence decision that is the CEO's.
- **Rule 2 — does NOT fire on its own:** build cost/schedule is small (reuses existing connector + CLI; no new build). Run cost: Atlassian API is included in the existing tenant (no new paid tier assumed) → ~no monthly cost delta. If a *paid* Atlassian tier or a reranker/embedding API were required, Rule 3 would fire again on cost — not the case for Confluence read API.
- **Rule 5 — does NOT fire:** no breaking change to a contract other teams use (MCP tool surface unchanged; this is an operational enablement).
- **Credential handling** is not its own escalation rule, but it is a HIGH-sensitivity security surface governed by L-001/E-003: the token must live only in env / `*_FILE`, never committed, never logged, scrub() on both the tool boundary and the error/log path. That is a design condition the SA/BE must honour, not a separate escalation.

**Verdict:** CEO's instinct is correct. Rules **1 + 3** fire → **ESCALATE to CEO Gate 1.** The CTO cannot open egress or authorise a new vendor/credential alone.

### 3. Scope decomposition (four parts, as the CEO framed them)

- **(a) Real-ingestion capability (egress for ingest only).** Relax no-egress **for the ingest pull path only**: `mcp-ingest run --source confluence` reaches `https://tnexwm.atlassian.net`. The 9 MCP servers + Jira MCP keep their invariants: read-only to source, stdio, no new port. Ingest already writes only to `kb.*` under `mcp_ingest_rw` (separate credential from the read-only query role) — the read-only-to-source invariant is preserved because ingest *reads* the source and *writes* the internal corpus; it never mutates Confluence. This is the smallest possible relaxation and must be scoped as "egress allow-list = the configured source hosts, nothing else."
- **(b) Credential / secret handling.** Atlassian Cloud API token for `tnexwm.atlassian.net` + account email + scopes. Stored via env / `*_FILE` only (never committed; `.env` is git-ignored, verified at the Oct-1 go-live). Doctor must validate the credential is **read-only-sufficient** and refuse to serve/ingest if the account can write (same pattern as the Jira/Confluence startup read-only check already built). Redaction via `scrub()` on tool-boundary + error/log path per L-001/E-003. Least-privilege Atlassian token (read scopes only). An ADR-deviation (SA) records the vendor + data-boundary change.
- **(c) CLI-driven integration RUNBOOK for all 9 sources, Confluence FIRST.** Each source maps to its existing CLI. **Two classes** (an honest runbook must say so):
  - **Ingested into the corpus (connector exists):** Confluence, GitLab, OpenSearch, Jira — flow = set creds → `mcp-<src> doctor` → `mcp-ingest run --source <src>` → `mcp-ingest status` → verify in pgvector / `kb_semantic_search`.
  - **Live read-only MCP only (no corpus ingestion today):** CloudWatch, Kibana, Kafka, Redis, SQS/SNS — flow = set creds → `mcp-<src> doctor` → register in `claude_desktop_config.json` → `tools/list` smoke. "Integrate" for these = reachable + read-only + registered, NOT ingested. Mapping: `mcp_confluence doctor`, `mcp_gitlab doctor`, `mcp_opensearch doctor`, `mcp_kibana doctor`, `mcp_cloudwatch doctor`, `mcp_kafka doctor`, `mcp_redis doctor`, `mcp_sqs_sns doctor`, `mcp_pgvector doctor` (+ `mcp_jira doctor`), plus the `mcp-ingest run|status` connector path for the four ingestable sources. **Confluence is step 1** end-to-end (doctor → ingest → semantic-search verify) as the reference pattern.
- **(d) HF-egress / NFR-003 (embedding model download) — note but keep separable.** Opening Atlassian does NOT open huggingface.co. Real Confluence content can be ingested, but it is embedded with the fake provider until HF egress + a real model (ADR-0010) are separately authorised. Note it: once *both* egresses are open, NFR-003 (DK1/D-002) finally becomes measurable — a bonus, not a requirement of CHG-003. Treat HF egress + ADR-0010 as a **separate** sub-decision at the same Gate 1 so the CEO can approve Atlassian-only, or both.

### 4. Gate 1 required? Track + first stage

- **Needs its own CEO Gate 1 — YES, but small/scoped (lean track).** Rules 1 + 3 force an escalation; opening egress + a vendor + a credential is squarely the CEO's Gate-1 right. It is NOT a full discovery — the capability is already built and reviewed; what the CEO is approving is a **boundary/policy change** (allow egress to named hosts + accept Atlassian as a vendor + accept storing a read-only token), not a new design. So: **lean track** — SA writes a focused **ADR-deviation** (baseline affected = platform: vendors + data-boundary; deviation ~86/100 itemised; invariant impact = no-egress + vendors=none; in-baseline option = "stay stubbed" and why it's insufficient; decision sought = accept egress to `*.atlassian.net` + Atlassian vendor + least-privilege token). No ≥3-options analysis needed (the fork is binary: open the real boundary or stay stubbed; the "how" is already decided by Option C). The CEO's Gate-1 answer can be one line per sub-decision: (i) Atlassian egress + token yes/no; (ii) HF egress + real model yes/no/later.
- **First stage to dispatch:** **squad-sa, mode design** — write the ADR-deviation for the egress/vendor/credential change (part a + b) and the credential-handling design, so the DM has a Gate-1 brief for the CEO. In parallel/after, **squad-sa or squad-ba** drafts the CLI runbook (part c) as an operational doc; it can be written now (it only documents existing CLIs) but must not instruct the CEO to use real creds until the CEO approves egress at Gate 1.

### 5. Provisional scope / cost / schedule + next role

- **Scope:** (a) egress-for-ingest policy + allow-list to configured source hosts; (b) Atlassian Cloud credential handling (read-only token, env/`*_FILE`, scrub, doctor refuses write-capable account); (c) CLI runbook for all 9 sources, Confluence first, two classes (ingest vs live-only); (d) HF/NFR-003 noted, separable.
- **Cost:** build ≈ 1–3 agent-days (reuse existing connector/CLI; main work is the ADR, the runbook doc, an egress allow-list guard + its adversarial test, and the doctor read-only credential check if not already enforced for Confluence Cloud live). Run cost delta ≈ $0 (existing Atlassian tenant; no new paid tier). **If** a paid tier or HF/model API turns out to be needed → Rule 3 fires again → back to CEO.
- **Schedule:** within the current envelope; no critical-path hit to anything already shipped. CHG-001 stays postponed by CEO choice (not blocked by CHG-003).
- **Next role to dispatch:** **squad-sa, mode `design`** — ADR-deviation (egress + Atlassian vendor + credential handling) for CHG-003, Confluence Cloud `https://tnexwm.atlassian.net` first; then DM assembles the Gate-1 brief for the CEO. Do NOT open egress, write secrets, or run live creds until CEO approves at Gate 1.

- **Checkers:** n/a (mode `blocked`; santa-method applies only to promote/cab-readiness).
- **Rationale:** CHG-003 relaxes two locked invariants (no-egress, vendors=none) by reaching a real external SaaS with a stored credential — two 40-point hard deviations, each far above the 10% threshold. Escalation rules 1 (drift outside approved option) + 3 (new vendor + egress) fire, so opening egress is a CEO Gate-1 decision, not CTO-alone — confirming the CEO's own framing. The capability is already built and reviewed, so the Gate 1 is a scoped boundary/policy approval on the lean track (ADR-deviation, no ≥3 options). The runbook must honestly split the 9 sources into 4 ingestable + 5 live-only; Confluence is the end-to-end reference. HF-egress/NFR-003 is a separable second sub-decision at the same gate.
- **Conditions / follow-ups:**
  - (C1) CTO does NOT open egress / write secrets / write state.json / plan-approval.md — DM owns those after the CEO's Gate-1 answer.
  - (C2) Invariants that must stay kept and be re-asserted in the ADR + an adversarial test (L-001): egress allow-list = configured source hosts only (deny all other outbound); the 9 MCP servers + Jira stay read-only-to-source and stdio (no new port); ingest writes only `kb.*` under `mcp_ingest_rw`.
  - (C3) Credential: least-privilege read-only Atlassian token, env/`*_FILE` only, never committed/logged, scrub() both ways; doctor refuses a write-capable account (reuse the Jira/Confluence startup read-only check).
  - (C4) Runbook states the two source classes explicitly; "integrate" for the 5 live-only sources = reachable + read-only + registered, not ingested.
  - (C5) HF egress + ADR-0010 model = separate sub-decision; keep CHG-003 approvable Atlassian-only. Once both egresses open, NFR-003 (DK1/D-002) becomes measurable — note, don't require.
  - (C6) CHG-001 remains postponed; its artifacts stay. DK2 (migration-locking) still precedes any next schema change on populated data.
- **Returned to:** none (ESCALATE to CEO; route forward = squad-sa mode design for the ADR-deviation, not a RETURN-for-defect).
- **Escalation:** rules **1** (work drifts outside approved option — Option C premise was no-egress) + **3** (new vendor Atlassian + data egress to `*.atlassian.net` + stored credential). Options for the CEO at Gate 1: **(A)** approve egress to `*.atlassian.net` + accept Atlassian as a vendor + a least-privilege read-only API token, enabling real Confluence-first ingestion via the CLI *(recommended — smallest real-value step, reuses everything built, invariants re-asserted + adversarial-tested)*; **(B)** also open HF egress + a real embedding model (ADR-0010) now so NFR-003 can be measured in the same pass *(optional, separable; bigger surface)*; **(C)** stay stubbed *(no real integration — refutes the CEO's stated goal)*. CTO recommendation: **(A)** now, **(B)** as an explicit separate yes/no at the same gate.

---

## D-007 · cab-readiness · 2026-10-02T13:15+07:00

(D3 — PRE exit → CAB for **CHG-003** real ingestion + real egress, CLI-driven, 9-source runbook, Confluence Cloud `https://tnexwm.atlassian.net` first; CEO Gate-1 Option B. `ENVIRONMENTS=uat,pre,prod` but this architecture has no shared UAT/PRE service — per-user local stdio — so D3 also covers the UAT exit criteria on the PRE-equivalent host, exactly as D-002 did for the base go-live.)

- **Decision: READY_FOR_CAB.**
- Tier: standard (CHG-003 is the lean/scoped change sized at D-006; it does not re-size the feature — no full-track signal; no change from the running "large" feature-level framing. For this change the depth is lean per D-006, so santa-method still runs **2 checkers** at D3 as required.)
- Evidence: `7-release/cab-pack.md` §1/§2/§3/§5/§6/§7/§9.0/§9.2/§9a, `6-verify/review-report.md` (CHG-003 round 1 APPROVE), `6-verify/regression-report-dev.md` (QA PASS), `records/errors.md` (E-001..E-009; E-008 open, E-009 verified), `2-gate1/plan-approval.md` (CHG-003 Option B), `.kiro/squad/config.env`, `scripts/squad/deploy.sh`, `8-gate2/cab-approval.md` (Oct-1 base), D-002/D-006 — detail:
  - `7-release/cab-pack.md` — §1 what changes, §2 scope (FR-023..027 delivered, 0% reduction; CHG-001 postponed not cut), §3 evidence, §5 deploy plan (backup-check before first real pull + the `SQUAD_CAB_APPROVAL=…/8-gate2/cab-approval.md deploy.sh prod` guard), §6 rollback (**< 60 s**, unset `MCP_EGRESS_ALLOWLIST` + remove token + stop connector; live egress defaults off), §7 observation window 30′ + SLI read-out mapping each rollback trigger to an SLI, §9.0 the five residual risks carried to the CEO, §9.2 open S3 (E-008), §9a baseline check (delta 0% scope, ≈$0/mo, schedule −20.8% vs D-006).
  - `6-verify/review-report.md` CHG-003 round 1 → **APPROVE**, 0 CRITICAL / 0 HIGH; 1 MEDIUM (R-C3-001) + 1 LOW (R-C3-002), both non-blocking residuals; reviewer independently reproduced the egress choke point at exactly three seams, token-scrub wiring at all 11 constructors, `verify_tool_surface` 50/50.
  - `6-verify/regression-report-dev.md` CHG-003 run 1 → **QA PASS**: `make ci` FULL exit 0, 2344 passed / 0 failed / 206 reasoned skips, coverage 90.08% (egress.py 98%, redact.py 94%), `validate-contract` OK, readonly 215/215; the four ADR-0023 §6e adversarial/invariant tests all hold; Confluence-Cloud e2e + TC-124 permission regression green on **real pgvector 0.8.6**; `@live` arms (TC-121/TC-131, live-register of TC-128) correctly skipped, never silently dropped.
  - `records/errors.md` + `scripts/squad/errors.sh open docs/squad/features/mcp-data-platform` → **no open S1/S2**; `errors.sh list` → 9 found, 8 closed, **1 open (E-008 S3, accepted)**; **E-009 (S2 security, token-scrub production wiring) verified-closed** this slice.
  - `2-gate1/plan-approval.md` CHG-003 (Option B — Atlassian **and** HF egress approved) + the kept invariants (read-only-to-source, stdio NFR-005 no new port, permission choke #1 + grounding gate #2 unchanged, ingest writes only `kb.*` under `mcp_ingest_rw`).
  - `.kiro/squad/config.env` (`DEPLOY_MODE=script`, `ENVIRONMENTS=uat,pre,prod`, `ESCALATE_COST_PCT=15`, `ESCALATE_SCHEDULE_PCT=20`); `scripts/squad/deploy.sh` prod guard; `8-gate2/cab-approval.md` (the **Oct-1 base** approval — scope = 9 read-only servers, does **not** cover CHG-003).
  - Precedent: `records/decisions.md` D-006 (CHG-003 sizing, lean), D-002 + DK1 (the NFR-003 risk-acceptance the CEO already made once), D-004/D-005 (τ-uncalibrated honesty).
- **Checkers: 2 independent checkers (santa-method). Both PASS.** (`use_subagent` is not available in this Kiro environment, so per the role instruction I ran the two READY/NOT-READY checks myself with the same rubric = the `squad-env-promotion` PRE→CAB exit criteria + the cab-pack evidence file list, read-only. Both recorded below.)
  - **Checker 1 — PRE→CAB exit-criteria rubric (objective gates):**
    smoke defined for prod ✔ (`smoke.sh prod` — doctor + ingest-path egress-default-deny + read-only surface + token-absent); every P1/every TC passes on the PRE-equivalent ✔ (`make ci` 2344/0; e2e 9/10 real-pgvector with TC-121 `@live` correctly skipped; TC-124 3/3; stdio read-only 20/20); every NFR threshold with a measured value **— with one honest exception: NFR-003 is UNVERIFIED (see Rationale/Conditions), carried to the CEO, not a measured fail**; security review 0 open CRITICAL/HIGH ✔ (review APPROVE); production audit = read-only surface audit `verify_tool_surface` 50/50 + `validate_contract` OK (no hosted service to audit) ✔; rollback rehearsed + measured time-to-rollback ✔ (**< 60 s**; shared-env rehearsal n/a — no shared service; the figure is the real cost of the documented unset-allowlist/remove-token/stop-connector steps, same pattern proven at the Oct-1 go-live); every rollback trigger maps to an SLI the watch can read with a PRE-equivalent read-out ✔ (cab-pack §7); **no open S1/S2** ✔; every open S3/S4 accepted by the CTO ✔ (E-008, this decision). → **PASS** (NFR-003 flagged as a CEO risk-acceptance, not a gate fail — same disposition as D-002).
  - **Checker 2 — judgement items + the five residuals:**
    coverage 90.08% ≥ 80% DoD ✔; the four ADR-0023 §6e invariant tests hold ✔ (egress default-deny opens no socket; allow-list fail-closed before dial through the single `check_egress`; token never leaks both ways + E-009 wired at all 11 constructors; 9 servers + Jira read-only + stdio, 0 new port); baseline delta 0% scope / ≈$0 run / schedule within envelope ✔; no shared-PRE substitution is the CEO-approved architecture (Gate 1), not a process skip ✔; risk rating medium matches the matrix ✔; residuals (1) NFR-003 unverified, (2) τ uncalibrated, (3) R-C3-001 MEDIUM, (4) E-008 S3, (5) real-tenant/Claude-Desktop manual steps — each assessed below, **none is a true S1/S2** ✔. → **PASS**, conditional on the CEO explicitly accepting residual (1) at Gate 2.
  - **Agreement:** both checkers PASS on round 1 — no disagreement, so no round 2 and **rule 6 does not fire**.
- **Rationale (≤5 lines):**
  1. Every objective PRE→CAB exit criterion is met with evidence pinned to the CHG-003 slice (commit `b898040` working tree): review APPROVE 0/0 HIGH, QA `make ci` 2344/0 + the four §6e adversarial tests green, Confluence-Cloud e2e + TC-124 on real pgvector, no open S1/S2, E-009 (S2) verified-closed.
  2. No shared UAT/PRE is **n/a by the CEO-approved design** (per-user local stdio); PRE-equivalent = real dev host + Docker pgvector 0.8.6, exactly as the two prior go-lives. Rollback **< 60 s** is the real cost of unsetting the allow-list + the default-off live-egress flag; no service to rehearse against.
  3. The only unverified NFR is **NFR-003 semantic recall** — blocked because both arms use the fake provider (real `bge-m3` + bake-off is the `@live`/spike-S2 step). This is **not** an S1/S2 defect; it is a quality residual of a Must, and accepting it is the **CEO's call at Gate 2**, the same class and the same disposition as DK1/D-002 which the CEO already accepted once.
  4. The other four residuals are all acceptable v1 residuals, not blockers: τ uncalibrated (τ-independent no-evidence⇒UNKNOWN / CONFLICT invariants hold regardless, and this surface is not even in this go-live — CHG-001 grounding is postponed); R-C3-001 MEDIUM (`HttpEmbeddingProvider` off-by-default, outside the approved `provider=local` path, pre-existing, falsifies no AC/NFR); E-008 S3 (pre-existing base FR-005 Kibana E2E gap, fixing it needs a forbidden mid-change TC renumber); real-tenant pull + Claude Desktop registration are manual operator Gate-C steps gated behind the default-off live-egress flag.
  5. No `squad-decision-rights` escalation rule fires to overturn READY_FOR_CAB: the vendor/egress deviation was already escalated and CEO-approved at Gate 1 (Option B); cost/schedule inside envelope; no new breaking contract; no open CRITICAL/HIGH security finding that can't be fixed before CAB.
- **Residuals — explicit block-vs-acceptable classification (what the brief asked):**
  - (1) **NFR-003 semantic recall UNVERIFIED** — **NOT an S1/S2. Acceptable v1 residual, carried to the CEO at Gate 2 for explicit risk-acceptance** (headline condition, same as DK1/D-002). Opening HF egress makes it *measurable*, not *proven*; `calibration_status=uncalibrated`, no number invented (L-002).
  - (2) **τ (FACT↔LOW_CONFIDENCE) uncalibrated** — **NOT S1/S2. Acceptable.** τ-independent honesty invariants hold; relevant only to the postponed CHG-001 grounding surface, carried for completeness.
  - (3) **R-C3-001 (MEDIUM) `HttpEmbeddingProvider` not egress-gated** — **NOT S1/S2. Acceptable residual** (off by default, outside the approved `provider=local` path, pre-existing, no AC/NFR falsified). Follow-up: harden before any `provider=http` use.
  - (4) **E-008 (S3, open) base FR-005 Kibana E2E gap** — **NOT S1/S2. Accepted by the CTO as a shipped residual** (this decision); pre-existing, non-blocking, covered by integration TC-019/020 + the stdio E2E; fixing needs a base TC renumber protocol §4 forbids mid-change.
  - (5) **Real-tenant pull + Claude Desktop registration (NFR-005)** — **NOT S1/S2. Acceptable.** Manual operator Gate-C steps gated behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true` (default off) + the CEO's real read-only token at run time.
  - **No residual is a true S1/S2.** None blocks READY_FOR_CAB.
- **Conditions / follow-ups (Gate-2 conditions for the CEO, DK-style):**
  - **(ĐK1 — the headline, for the CEO at Gate 2):** the CEO/PO must **explicitly accept** that CHG-003 ships with **NFR-003 semantic recall unproven** (fake provider on both arms; real measurement is the `@live` bge-m3 download + spike-S2 bake-off). Same acceptance the CEO made at D-002/DK1. If not accepted, go-live waits for the bake-off.
  - **(ĐK2 — post-cut-over gates, must be true after the deploy):** `smoke.sh prod` green **and** the 30-minute observation window green (liveness + egress-default-deny invariant + token-never-leaks invariant per cab-pack §7); **any rollback trigger → immediate rollback (`rollback.sh prod`, < 60 s, no approval needed) → `incident` decision + a NEW Gate 2** before the next production attempt.
  - **(ĐK3 — production-deploy guard, confirmed):** a **NEW CHG-003** `8-gate2/cab-approval.md` with `status: approved` is required before `deploy.sh prod` runs. I verified the guard (`deploy.sh` greps `^status: approved`) and that the existing Oct-1 `cab-approval.md` **does not satisfy it** (grep returns no match **and** its scope is the base 9-source go-live) — so the Oct-1 approval does not cover this change, by content and by scope. The DM/orchestrator writes the new file after Gate 2; the CTO does not.
  - **(ĐK4 — follow-ups to carry):** harden R-C3-001 (route `HttpEmbeddingProvider` through the egress guard) **before** any `provider=http` use; fix E-008 (add a dedicated FR-005 Kibana E2E TC) at the next change that may renumber base TCs; DK2 (migration-locking) still precedes any next schema change on populated data — but CHG-003 adds **no** migration, so it does not trip DK2; DK3 (Redis dev ACL never shared) stands.
  - **(ĐK5):** before the first real pull, confirm and **log the id** of a restorable Postgres snapshot (cab-pack §5 step 2) — real content enters the corpus for the first time.
- **Returned to:** none.
- **Escalation:** none — no `squad-decision-rights` rule fires to overturn the verdict (the egress + Atlassian-vendor + HF-model deviation was already escalated and CEO-approved at Gate 1 Option B; cost/schedule inside envelope; no new paid vendor beyond the approved ones; no breaking contract change; no open CRITICAL/HIGH). NFR-003 is **not** an escalation — it is a Gate-2 CEO risk-acceptance (ĐK1), carried, with my recommendation to accept for v1 (read-only surface, no data-loss path, measurement enabled by this very change) exactly as at D-002.
- **Next step:** orchestrator takes the CAB pack to the **CEO at Gate 2**. If approved, the orchestrator writes the NEW CHG-003 `8-gate2/cab-approval.md` (`status: approved`), then release deploys under the guard and runs smoke + the 30-minute watch.


## D-008 · blocked · 2026-10-02T15:33+07:00

- **Decision: APPROVE** — invoke the documented escape-hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` so the
  CHG-003 local go-live serves/ingests with the CEO's write-capable Atlassian token while the system limits
  itself to read-only (0 write tools). Re-run deploy from Step 1; doctor will WARN-and-serve.
- Evidence: `records/errors.md` E-009; `8-gate2/cab-approval.md`; D-007 — `runtime.py` escape-hatch WARN path
  (lines 188/209/212/217), `config.py#allow_unverified_credentials`, `mcp_confluence/client.py` (`@readonly_tool`,
  0 write ops), `evidence/deploy-prod/20261002-150938-chg003-confluence-doctor.txt` (write-capable refusal) — detail below.

**Context.** CHG-003 go-live blocked at deploy Step 1: `mcp-confluence doctor` refused the CEO-supplied
Atlassian token because the account is **write-capable** (create/update page, comment, attachment, etc.).
The read-only startup gate (ADR-0003 A1 / ADR-0007 A2, TC-116) worked as designed.

**CEO decision.** CEO chose Option 2 explicitly: "token có quyền ghi nhưng trong hệ thống sẽ giới hạn chỉ
cho read-only" — invoke the documented escape-hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` so the server
serves/ingests with the write-capable token while the system limits itself to read-only.

**What this does / does not change.**
- Does NOT change the tool surface: all 9 servers still expose **0 write tools** (`@readonly_tool`,
  `x-readonly:true`, verify_tool_surface 50/50). There is **no code path that writes to Confluence** via MCP.
- Changes ONLY the startup credential check: instead of refusing to serve a write-capable account, the
  runtime logs ONE WARN (`MCP_ALLOW_UNVERIFIED_CREDENTIALS=true — serving anyway`) and proceeds
  (mcp_common/runtime.py:209).
- Ingest still pulls read-only (GET/HEAD) and writes only to `kb.*` under `mcp_ingest_rw`.

**Residual risk (CEO-accepted).** Storing a credential stronger than needed: if the token leaks it could
be used to modify the company wiki — but NOT through this system (no write path). Token-leak is mitigated by
the E-009 scrub-both-ways wiring (register_secret at client construction). Accepted for this phase.

**CTO conditions.**
- The read-only guarantee now rests entirely on the tool-surface invariant (0 write tools) + the
  permission/grounding choke points, NOT on the credential being read-only. The adversarial read-only
  surface tests (TC-125/126, assert_readonly_tool_surface) remain the enforced gate.
- Recommend replacing with a least-privilege read-only service-account token when feasible (revisit before
  any multi-user / shared deployment; never reuse this write-capable token beyond the CEO's local machine).
- This is a scoped escape-hatch invocation under the already-approved CHG-003 Gate-2; no new Gate 2 needed
  (nothing about egress/vendor/scope changed; the token was always going to be the CEO's at run time).

---

## D-009 · retro · 2026-10-02T22:30:00+07:00

(Blameless retro đóng feature — CHG-003 go-live + cửa sổ theo dõi đóng GREEN; CEO chọn đóng feature và giữ NFR-003 ở mức "đã đo lần đầu, cỡ mẫu nhỏ".)

- **Decision: ACK** (viết `9-retro/retro.md` phần đóng feature; append L-004 + L-005 vào `knowledge/lessons.md`).
- Tier: large (không đổi — không re-size; CHG-003 là change lean trên feature large; không có tín hiệu re-size mới).
- Evidence: `state.json` history (hai mốc chg003-watch cuối: NFR-003 đo lần đầu 2026-10-02T22:18 + watch-close GREEN 22:25); `records/decisions.md` D-001..D-008; `records/errors.md` E-001..E-009 (`errors.sh open` = no open S1/S2; `errors.sh list --feature mcp-data-platform` = 9 found/8 closed/1 open E-008 S3); `7-release/release-log.md` (deploy EA bounded pull + watch-close GREEN); `docs/squad/knowledge/lessons.md` L-001..L-003; `knowledge.sh due` = not due (0/3 finished feature, 3/40 active lesson).
- Checkers: n/a (mode `retro`; santa-method chỉ bắt buộc ở promote/cab-readiness).
- Rationale (≤5 dòng):
  1. Feature đạt go-live thật (CHG-003: egress thật tới `*.atlassian.net` + ingest EA thật) với **0 defect escaped ra live env, 0 rollback, 0 incident**; cửa sổ 30′ đóng GREEN với 5 trigger (liveness, số dòng, egress default-deny, token-không-rò, error rate) đều xanh.
  2. NFR-003 lần đầu đo bằng model thật (bge-m3): hit@5 8/8, MRR 1.0 trên 1 doc EA (25 chunk) — **đo thật nhưng cỡ mẫu nhỏ**; giữ ở mức "đã đo lần đầu (cỡ mẫu nhỏ)", chưa "verified trên dữ liệu công ty" (đúng L-002, không thổi phồng).
  3. Lặp lại rõ của class L-001: E-007 (permission 2/8 tool) + E-009 (register_secret built-but-unwired) — cả hai đã fixed+verified; seed hai lesson mới L-004 (mechanism built ≠ wired; enumerate mọi đường) và L-005 (biên giới mới → default-deny choke point + adversarial test cả hai chiều + ghi residual off-by-default).
  4. Không có S1/S2 mở; residual duy nhất là E-008 (S3) đã CTO-accept ở D-007; các residual khác (R-C3-001, NFR-003 full, CHG-001 postponed, 2 lỗi layout) là known/carried, không chặn đóng feature.
  5. Distill KHÔNG due.
- Residual / carried-forward (ghi rõ khi đóng feature):
  - **E-008** (S3, test, OPEN/deferred) — thiếu E2E TC cho Must FR-005 (Kibana); CTO-accepted (D-007); sửa ở change kế tiếp cho phép renumber base TC.
  - **R-C3-001** (MEDIUM) — `HttpEmbeddingProvider` tắt-mặc-định, chưa bọc egress guard → **DK4: harden trước khi dùng provider=http**.
  - **NFR-003 full verification** — cần corpus EA lớn hơn + golden-set đã kiểm chứng + hiệu chỉnh τ; hiện mới "đo lần đầu (cỡ mẫu nhỏ)".
  - **CHG-001 Company Knowledge** — vẫn **POSTPONED** (artifact/ADR giữ; DK2 migration-locking đã xong; DK3 Redis ACL không tái dùng cho shared còn hiệu lực).
  - **Hai lỗi layout-check pre-existing** (release role ghi): `records/backlog.md` ngoài layout + một số file evidence/probe (`qa-dev` probe + driver `deploy-prod` + `__pycache__`) không đúng tên `<YYYYMMDD-HHMMSS>-<kebab>.<ext>` → `layout.sh check` FAIL; cần dọn, không chặn.
- Lessons added: **L-004** (all — mechanism built ≠ mechanism wired; enumerate mọi đường + test wiring production thật) [E-009, E-007]; **L-005** (squad-cto — biên giới mới → một default-deny choke point + adversarial test cả hai chiều + ghi residual off-by-default) [E-009, R-C3-001, ADR-0023 §6e].
- Conditions / follow-ups: khi mở lại CHG-001 go-live → DK2 (đã xong) + DK3 (Redis ACL) + DK4 (harden R-C3-001) + đo NFR-003 trên corpus lớn hơn; dọn 2 lỗi layout + fix E-008 ở change tiếp theo; đặt `TOKEN_BUDGET_M` + wire `ecc:cost-tracking` để retro sau báo được spend/stage.
- Returned to: none.
- Distill: NOT due — `knowledge.sh due` = 0/3 finished feature kể từ distill trước, 3/40 active lesson (nay 5 sau L-004/L-005). Không distill lượt này.
