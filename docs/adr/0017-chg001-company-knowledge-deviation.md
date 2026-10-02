# ADR-0017: CHG-001 Company Knowledge layer lệch khỏi baseline đã duyệt Gate A/B (deviation)

**Date**: 2026-10-01
**Status**: **accepted** (2026-10-01, CEO Gate 1 — `2-gate1/plan-approval.md`) — CEO duyệt **Option C** ("tôi đồng ý toàn bộ khuyến nghị"). Phần "Decision sought" dưới đây giữ nguyên làm bản ghi lựa chọn; kết quả = lựa chọn **3 (Option C)**. Xem **Decision taken (Gate 1)** ngay dưới Status.
**Deciders**: SA (squad-sa) đề xuất; CTO xác nhận deviation LARGE ở D-001; **CEO quyết** (mở/giữ baseline)

## Decision taken (Gate 1, 2026-10-01)
CEO chọn **Option C — accept deviation ở mức trung dung** (lựa chọn 3 trong "Decision sought"):
- Company MCP **gateway-boundary IN-PROCESS, GIỮ NFR-005 stdio-only** (không service HTTP; HTTP+SSE hoãn v1.1).
- Jira là **nguồn thứ 10** (thin REST read-only, ADR-0007, flavor Cloud/Server-DC).
- **Hybrid-RAG trong một Postgres** (tsvector + pgvector + metadata + RRF) + **reranker local offline** (`bge-reranker-v2-m3`, `HF_HUB_OFFLINE=1`, RRF-only fallback có cờ) — **vendors=none giữ nguyên, không egress**.
- Relationship bằng **recursive CTE** (không AGE, không graph DB riêng).
- 4 domain dữ liệu Postgres mới (versions / entities+relationships / summaries / permissions).
- **Permission server-side trước context assembly** (ADR-0016, đảo giả định C6/BR-003 — invariant business được amend tại Gate 1).
- B4 Grounding/Evidence **in-scope** CHG-001 (CTO D-004) — enforce tại chính context-pack assembler (ADR-0018).

**Hệ quả baseline (CEO chốt amend — plan-approval.md §"four Gate-1 decisions" #4):** `platform-baseline.md` + `business-baseline.md` được bổ sung Jira (source #10), Hybrid-RAG + reranker local, gateway-as-boundary in-process (giữ stdio), 4 domain dữ liệu mới, permission server-side. Việc amend baseline do orchestrator/CTO thực hiện ở inheritance-loop sau go-live (`squad-baselines`); ADR này là bản ghi quyết định.

**KHÔNG được chọn ở Gate 1:** Option B (service HTTP ContextForge phá NFR-005 + Apache AGE) và DP2 reranker API trả tiền (vendors=none giữ nguyên). Nếu build phát hiện cần vendor/egress/service HTTP mới ⇒ ESCALATE CTO/CEO, không âm thầm thêm.

> ADR-deviation bắt buộc theo skill `squad-baselines`. Nó ghi **CHG-001 lệch khỏi option đã duyệt ở Gate A/B
> thế nào và vì sao**, đặc biệt **NFR-005 stdio-only vs Company MCP Gateway**. Đi kèm
> [`1-discovery/options.md`](../squad/features/mcp-data-platform/1-discovery/options.md) (A/B/C) và D-001
> (`records/decisions.md`).

## Status
**accepted** (2026-10-01, CEO Gate 1). CEO duyệt **Option C** ("tôi đồng ý toàn bộ khuyến nghị" —
`2-gate1/plan-approval.md`). Ba lựa chọn ứng với ba option trong `options.md` giữ nguyên bên dưới làm bản ghi;
kết quả là **Option C** (gateway-boundary in-process giữ stdio; không chọn B service-HTTP, không chọn A hoãn
gateway). CTO đã chấm LARGE và escalate (D-001). Build CHG-001 Option C bắt đầu sau quyết định này.

## Baseline affected
**Cả platform và business.**

> Lưu ý: `platform-baseline.md` và `business-baseline.md` còn ở trạng thái template (các mục `TBD`,
> `vendors=none`). Theo `squad-baselines` ("Bootstrapping"), baseline **hiệu lực** để chấm deviation là
> **option đã duyệt ở Gate A/B** của `mcp-data-platform`: 9 MCP server read-only, **stdio-only** (NFR-005,
> ADR-0002), mỗi nguồn một process, **không service phụ** (ADR-0003 Alt 3 đã bác proxy chung), pipeline
> ingest/embedding → **một** Postgres+pgvector, **semantic search đơn giản**, **no RBAC per-user** (ADR-0016:
> kb team-only), provenance/trace đã có.

**Platform — các entry bị chạm:**
- *Approved architecture patterns:* thêm **Company MCP Gateway** (routing/auth/authz/policy/audit/rate-limit)
  + tách Knowledge/Live/Action MCP → đổi mô hình truy cập từ "stdio trực tiếp, mỗi nguồn một process" sang
  **gateway-fronted**. Chạm trực tiếp **NFR-005 (stdio-only)** và ADR-0003 Alt 3.
- *Approved architecture patterns:* thêm **Hybrid-RAG engine** (vector+keyword+metadata+relationship+RRF+
  reranker+context-compression+context-pack) thay cho "semantic search đơn giản" — một retrieval subsystem mới.
- *Approved datastores:* 4 domain dữ liệu mới trong Postgres (`document_versions`, `entities`+`relationships`,
  `knowledge_summaries`, `document_permissions`); + (nếu Option B) **Apache AGE** extension graph = pattern/
  datastore mới.
- *Approved vendors & paid services:* **vẫn `none`** nếu đi Option A/C hoặc Option B với ContextForge
  (Apache-2.0, OSS, không trả phí). **Chỉ trở thành vendor trả phí nếu** CEO mở DP2 reranker API
  (Cohere/Voyage) hoặc context-compression LLM-API — khi đó + egress + vendor.
- *Data-boundary rules:* reranker/embedding **phải nạp offline** (`HF_HUB_OFFLINE=1`) để giữ "dữ liệu nội bộ
  không ra ngoài"; mọi phương án API = **egress** mới.

**Business — các entry bị chạm:**
- Nguồn thứ **10 (Jira)** — Must-have mới ngoài 9 nguồn baseline (ingestion + Live MCP).
- **Đảo giả định C6/BR-003** "mọi thành viên quyền như nhau, no RBAC per-user": spec §24/§43 đòi **permission
  enforce server-side trước context assembly**. Đây là một **invariant mới về bảo mật** cần CEO chốt.
- Năng lực mới: document versioning (lịch sử/rollback), entities/relationships (graph nghiệp vụ), knowledge
  summaries, Live-vs-Knowledge + freshness/offline + conflict/source-authority (hallucination control).

## Deviation score
**100/100 (cap).** Itemised (theo thang `squad-baselines`, khớp D-001):

**Hard:**
| Deviation | Điểm | Lý do |
|---|---|---|
| New architecture pattern | 20 | Company MCP Gateway + tách Knowledge/Live/Action → đổi access-model, chạm NFR-005 & ADR-0003 Alt 3. |
| New architecture pattern | 20 | Hybrid-RAG engine (RRF+rerank+compression+context-pack) thay semantic search đơn giản — subsystem mới. |
| Breaking change tới contract đang dùng | 30 | Access-model qua gateway đổi bề mặt MCP mà client (Claude Desktop/Code đăng ký N server stdio) đang dựa; permission server-side (§43) đảo giả định C6 "quyền thừa hưởng tại máy user". |
| New datastore domain / (Option B) extension | 25 | 4 domain Postgres mới (versions/entities+relationships/summaries/permissions); Option B thêm Apache AGE extension. |
| New paid vendor / data egress | 0 (A/C, B-OSS) / **+40 (nếu mở DP2 API)** | Reranker local offline + ContextForge OSS ⇒ 0. Chỉ +40 nếu CEO mở reranker/compression **API trả tiền** (egress + vendor). |
| **Soft (CTO judgement)** | +12 | Tái dùng nhiều (connector Confluence/GitLab, pgvector store, pipeline ingest/embedding, chunk model, read-only defense) **nhưng** drift kiến trúc rất lớn: 5–6 tầng mới chồng lên nền, đảo C3/C6, retrieval & live/knowledge split là subsystem mới. |
| **Tổng (cap 100)** | **100** | ≫ ngưỡng 10. Mỗi hard-deviation đơn lẻ đã tự vượt ngưỡng. |

> Theo option: **A** và **C** không thêm vendor/egress/service HTTP (A hoãn gateway; C gateway in-process giữ
> stdio) → điểm hard chủ yếu từ pattern + datastore domain + breaking-change. **B** cộng thêm pattern (gateway
> service HTTP phá NFR-005) + datastore (AGE). Dù option nào, tổng vẫn cap 100 vì nhiều hard-deviation chồng
> lên nhau; **điểm khác nhau là loại rủi ro (NFR-005/SPOF/egress), không phải con số** — đó mới là thứ CEO quyết.

## Invariant impact
- **NFR-005 (stdio-only) — invariant kỹ thuật cốt lõi.** Option **B phá** (service HTTP ContextForge `:4444`,
  chưa xác minh nhúng stdio không mở cổng — UNVERIFIED). Option **A giữ** (hoãn gateway). Option **C giữ**
  (gateway là module in-process, không mở cổng mạng). → **điểm quyết chính của CEO.**
- **BR-001/NFR-001 (read-only tuyệt đối) — invariant bảo mật, KHÔNG được phá ở bất kỳ option nào.** Mọi tool mới
  (Knowledge, Live Jira) đi qua đúng choke point `mcp_common` 5 lớp (ADR-0003) + adversarial test (L-001). Jira
  dùng thin REST read-only (ADR-0007), bề mặt ghi = 0.
- **C6/BR-003 (no RBAC per-user, quyền đồng nhất) — invariant business bị ĐẢO có chủ đích.** Spec §24/§43 đòi
  permission server-side. Đây là **invariant mới** CEO phải chốt (amend business-baseline) — theo
  `squad-baselines`, chạm invariant là escalation bất kể điểm số.
- **"Postgres là một knowledge store" (spec §4.4).** A/C giữ (một DB + CTE). B thêm AGE extension (vẫn trong
  Postgres nhưng pattern graph riêng).

## In-baseline options considered
- **Ở lại hoàn toàn trong baseline = không làm CHG-001** (Baseline "do nothing" trong `options.md`): mâu thuẫn
  quyết định CEO Gate-1 Option A ở D-001 (đã chấp nhận CHG-001 về nguyên tắc, xếp sau go-live scope cũ). Chi phí:
  spec Company Knowledge không được đáp ứng; NFR-003 vẫn UNVERIFIED.
- **Gần baseline nhất mà vẫn làm = Option A** (hoãn Gateway §6 sang v1.1, giữ stdio, tất cả in-Postgres): thỏa
  mọi Must trừ Gateway §6; deviation vẫn LARGE vì Hybrid-RAG + 4 domain mới + đảo C6 là bắt buộc của spec. →
  cho thấy **không có cách nào làm CHG-001 mà deviation < ngưỡng**; vì vậy ADR-deviation + CEO Gate 1 là bắt buộc.
- **Giữ invariant quan trọng nhất (NFR-005) mà vẫn đủ §6 = Option C** (gateway-boundary in-process): đáp ứng
  trách nhiệm §6 ở tầng process **mà không** mở service HTTP → giữ stdio, không SPOF, không vendor. Đây là cách
  "ít rời baseline nhất trong các cách làm-đủ-spec".

## Decision sought
CEO chọn một trong ba (mặc định SA/CTO khuyến nghị thứ ba):
1. **Accept deviation ở mức Option B** — chấp nhận **phá NFR-005** (service HTTP ContextForge) + AGE; mở
   baseline sang "gateway service HTTP + graph extension". *(Chỉ nên nếu CEO muốn multi-user/remote + SSO đầy
   đủ ngay v1, chấp nhận SPOF + spike + lock-in.)*
2. **Stay-in-baseline-nhất bằng Option A** — hoãn Gateway §6 sang v1.1, giữ stdio; baseline chỉ mở phần
   Hybrid-RAG + Jira + 4 domain + permission server-side.
3. **Accept deviation ở mức Option C (khuyến nghị)** — mở baseline sang: Jira (nguồn 10), Hybrid-RAG + reranker
   local offline, **gateway-as-boundary in-process (GIỮ NFR-005 stdio)**, 4 domain dữ liệu mới, permission
   server-side (amend invariant C6/BR-003). **Không** vendor/egress/service HTTP/datastore mới.

Trong mọi trường hợp: **DP2 reranker mặc định local offline** (vendors=none); CEO quyết riêng nếu muốn mở
deviation +40 cho API trả tiền. **NFR-003 chấp nhận UNVERIFIED** tới khi đo trong CHG-001 (nối DK1/D-002).

## Cost & risk
- **Build / run (vs baseline Gate-A/B, $0 run):** A 18–26 ngày-agent · B 30–46 (+ spike) · C 22–32. Run:
  A/C **$0** ngoài hạ tầng hiện có; B **$0 licence** nhưng +1 service HTTP + 1 extension graph = operating
  burden + 1 SPOF cho công ty một người.
- **Lock-in:** A/C thấp (code nội bộ + Postgres, đảo dễ); B cao (ContextForge config/plugin + AGE graph data).
- **Security:** 1–2 bề mặt bảo mật mới (permission filter; +gateway/AGE ở B). Rủi ro HIGH nếu permission filter
  sai (lộ tài liệu `restricted`) — giảm thiểu bằng **một choke point + adversarial test** (L-001) và token crawl
  quyền thấp (ADR-0016 A2 default-deny). DK3: Redis ACL không tái dùng shared.
- **Migration an toàn:** DK2 (R-006/R-007) phải fix **trước** E1 (lần đổi schema đầu trên dữ liệu prod đã có):
  `NOT VALID` + `CREATE INDEX CONCURRENTLY` ngoài transaction per-file.
- **Egress:** reranker/embedding **phải** offline; API = egress mới (chỉ nếu CEO mở DP2).

## Recommendation
**Option C (accept deviation ở mức trung dung).** Lý do:
1. Đáp ứng **đủ nghiệp vụ spec §55 + trách nhiệm Gateway §6 ở tầng process** mà **giữ invariant NFR-005
   stdio-only** và "một store Postgres" — rủi ro loại "phá bất biến/SPOF/egress" nhỏ nhất trong các cách làm-đủ.
2. **Chi phí/thời gian gần A** ($0 run, 22–32 ngày-agent), **vendors=none được tôn trọng** (không service HTTP,
   không AGE, không API), nhưng **đặt sẵn đường mở HTTP+SSE v1.1** (chỉ đổi transport trước `mcp_gateway`, không
   viết lại policy) — tốt hơn A về strategic fit.
3. An toàn đúng bài học feature: **một** choke point cho read-only (L-001) **và** permission server-side
   (§24/§43) + adversarial test; không egress; gánh DK2/DK3 trước build.

Nếu CEO ưu tiên SSO/audit/remote đầy đủ ngay v1 và chấp nhận SPOF + spike + lock-in → chuyển sang Option B
(và chấp nhận deviation NFR-005 tường minh ở đây). Nếu CEO muốn ship nhanh nhất và hoãn Gateway → Option A.
