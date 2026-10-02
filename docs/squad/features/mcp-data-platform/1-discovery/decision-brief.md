# CHG-001 Company Knowledge — Decision Brief cho CEO (Gate 1)

> Feature: `mcp-data-platform` · CHG-001 (Company Knowledge trên nền 9-nguồn read-only đã go-live local) ·
> tier large · squad-po · 2026-10-01. Nguồn: `1-discovery/options.md`, `docs/adr/0017-...deviation.md`,
> `1-discovery/market-research.md`, `records/decisions.md` (D-001/D-002/D-003), `9-retro/retro.md`.

## TL;DR
- Vấn đề: 9 nguồn đã live nhưng chưa trả lời được câu hỏi nghiệp vụ (Jira, hybrid-RAG, versioning, quan hệ, tóm tắt, phân quyền server-side); CHG-001 bổ sung tầng này.
- Khuyến nghị **Option C** (gateway MỎNG in-process **giữ** stdio + Postgres hybrid + reranker local offline): đủ nghiệp vụ, giữ bất biến NFR-005, **không** vendor/service/datastore mới.
- Chi phí **$0/tháng** ngoài hạ tầng hiện có; **~20–28 ngày-agent**; go-live sau khi đo được NFR-003.

## Problem & goal
Nền 9-nguồn (ADR-0002/0003/0011) đã go-live local 2026-10-01, read-only invariant giữ nguyên, 0 defect thoát ra
live. Nhưng spec Company Knowledge chưa đáp ứng: không Jira, chỉ semantic search đơn giản, v1 giả định mọi người
quyền như nhau. Mục tiêu CHG-001: thêm tầng Company Knowledge (Jira, Hybrid-RAG, versioning, entities/
relationships, summaries, permission server-side) **mà không phá** bất biến đã duyệt. CEO đã chọn Option A ở
D-001 (đóng Gate C scope cũ trước — đã xong), nay mở CHG-001 làm Gate-1 mới.

## What we found
- DP1 Gateway: OSS gateway (IBM ContextForge Apache-2.0) đều **HTTP-first** → mâu thuẫn NFR-005 stdio-only; ContextForge nhúng stdio không mở cổng hay không vẫn **UNVERIFIED, cần spike** [DP1, nguồn 1].
- DP2 Reranker: `bge-reranker-v2-m3` Apache-2.0 cùng họ embedding, chạy local; API (Cohere/Voyage) = egress + vendor trả phí (vendors=none) [5,6].
- DP3/DP4: tsvector+pgvector+RRF và recursive CTE đủ cho hybrid + graph nông 2–3 hop trong **một** Postgres [7–15]. DP5 Jira: thin REST read-only client theo ADR-0007, bề mặt ghi = 0 [19–22].
- Số đo thật DP2/DP3 vẫn **UNVERIFIED** do egress HF 403 (kế thừa S2/S3, L-002).

## Inheritance & platform deviation
Nếu duyệt C, baseline kế thừa: Jira (nguồn 10), Hybrid-RAG + reranker local offline, **gateway-as-boundary
in-process (giữ stdio)**, 4 domain dữ liệu mới Postgres (versions/entities+relationships/summaries/permissions),
permission server-side (đảo C6/BR-003). **Deviation = 100/100** (ADR-0017: +20 gateway, +20 Hybrid-RAG, +30
breaking access-model, +25 datastore). **Invariant chạm:** NFR-005 (C giữ, B phá), C6/BR-003 (đảo có chủ đích).
→ **ADR-deviation + CEO quyết bắt buộc**. Mở DP2 API thì +40 egress/vendor.

## Options
| Option | Nội dung | Go-live | Chi phí (build / run tháng) | Rủi ro chính | Điểm |
|---|---|---|---|---|---|
| **A** | Hoãn Gateway v1.1; giữ stdio; all-in-Postgres; reranker local | ~16–22d | 18–26 ngày-agent / **$0** | thiếu §6 Gateway; permission rải trong từng tool | 440/500 |
| **B** | ContextForge service HTTP + Apache AGE graph; full spec §55 | ~30–42d | 30–46 ngày-agent / **$0 licence + 1 service&graph burden + SPOF** | **phá NFR-005**, SPOF, spike/licence UNVERIFIED | 290/500 |
| **C** ✅ | Gateway MỎNG in-process **giữ stdio** + Postgres hybrid + CTE + reranker local | ~20–28d | 22–32 ngày-agent / **$0** | 2 guarantee mới (read-only + permission) phải có 1 choke point | **445/500** |

(Chi phí "as of 2026-10-01"; run $0 = không licence/vendor/service mới ngoài hạ tầng đang chạy.)

## Recommendation
**Option C.** Vì: (1) đủ nghiệp vụ spec §55 gồm Gateway §6 ở tầng process **mà giữ** NFR-005 stdio-only và "một
store Postgres" — rủi ro phá-bất-biến/SPOF/egress nhỏ nhất; (2) chi phí/thời gian gần A ($0 run) nhưng đặt sẵn
`mcp_gateway` để mở HTTP+SSE v1.1 chỉ cần đổi transport, không viết lại policy; (3) một choke point cho read-only
(L-001) + permission server-side (§24/§43) + adversarial test. **Đổi ý** nếu: spike xác nhận ContextForge nhúng
stdio + CEO muốn multi-user/remote + SSO ngay v1 (→ B); hoặc đo thật thấy CTE chậm (→ đổi riêng DP4 sang AGE).

## Plan (Option C)
Epic trên nền hiện tại (D-001), environments `uat,pre,prod` (per-user local stdio):
- E1 Knowledge Store schema+ (3–4d) — **chặn**: fix DK2 migration-locking trước (R-006/R-007).
- E2 Jira connector + Live Jira MCP (2–3d) · E3 Hybrid-RAG + reranker local (4–6d) · E4 Knowledge MCP tools (3–4d).
- E5 Live-vs-Knowledge + freshness/offline (2–3d) · E6 permission server-side (2–3d) · E8 eval/telemetry (2–3d).
- Gateway-boundary `mcp_gateway` in-process (4–6d). **Tổng ~20–28 ngày-agent** (TOKEN_BUDGET_M=0 chưa đặt — wire cost-tracking trước build theo DK/retro).

## Go-live criteria
- Read-only invariant giữ: adversarial test (tool "ghi" bị bác) **42/42+** pass, 0 mutate path reachable.
- Permission server-side: caller không-quyền **không** thấy tài liệu `restricted` **trước** context-pack.
- **NFR-003 đo thật**: recall@k / NDCG trên corpus thật sau khi gỡ egress HF (ngưỡng chốt ở design), hết UNVERIFIED.
- Reranker/embedding chạy **offline** (`HF_HUB_OFFLINE=1`), 0 egress. BE coverage ≥ **80%**; 0 open S1/S2.

## Budget & escalation
Baseline: $0/tháng run, ~20–28 ngày-agent build. CTO escalate lên CEO nếu vượt `ESCALATE_COST_PCT=15%` /
`ESCALATE_SCHEDULE_PCT=20%`, hoặc phát sinh vendor/egress mới (vendors=none), hoặc chạm thêm invariant.
CHG-001 deviation = 100 (> `DEVIATION_THRESHOLD_PCT=10`, đã escalate D-001).

## Track
**large (full)** — giữ theo D-001/D-002/D-003 (deviation LARGE, nhiều tầng mới, 2 guarantee bảo mật mới). CEO
có thể đổi track tại Gate 1; không khuyến nghị hạ vì permission server-side cần full depth. Safety invariant
(read-only, offline, một choke point) không track nào được nới.

## Risks
1. Chất lượng reranker/hybrid **UNVERIFIED** tới khi gỡ egress HF — M; mitigation: fallback RRF-only có cờ (L-002), đo thật trong E3 trước go-live.
2. Permission filter sai → lộ `restricted` — M/H; mitigation: một choke point `mcp_gateway.enforce` + adversarial test + default-deny (ADR-0016).
3. Gateway tự viết (không sẵn như ContextForge) — M; mitigation: §6 ở mức local, SSO đầy đủ hoãn v1.1.
4. Migration trên dữ liệu đã có — M; mitigation: fix DK2 (`NOT VALID`, index CONCURRENTLY) trước E1.
5. CEO muốn remote/SSO ngay v1 → phải sang B — mitigation: quyết tường minh ở Gate 1 dưới.

## Decisions needed from the CEO
1. **Chọn option (DP1/NFR-005 — quyết định lớn nhất):** **C** (gateway in-process, **giữ** stdio — khuyến nghị) / **B** (chấp nhận deviation NFR-005, service HTTP ContextForge + AGE) / **A** (hoãn Gateway v1.1). → mở/giữ baseline qua ADR-0017.
2. **DP2 reranker:** giữ **reranker local offline** (khuyến nghị, vendors=none) — hay mở deviation +40 cho API trả tiền (khi đó PO lấy giá "as of" từ trang pricing)?
3. **NFR-003:** chấp nhận **UNVERIFIED tới khi đo được trong CHG-001** (nối DK1/D-002) — hay chặn build tới khi gỡ egress HF trước E3?
4. **Baseline inheritance (nếu duyệt C):** chốt vào baseline: Jira (nguồn 10), Hybrid-RAG + reranker local, gateway-as-boundary in-process, 4 domain dữ liệu mới, permission server-side (amend invariant C6/BR-003)?
