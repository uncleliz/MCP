# CHG-002 "AI hiểu công ty" — Feasibility Research (10 building blocks + Orchestrator/Harness)

> Feature: `mcp-data-platform` · Change: **CHG-002** (enhancement layered on top of CHG-001 Option C — approved)
> · Mode: **research / feasibility** (tier=large) · Author: squad-researcher · Date: 2026-10-01
> Scope ràng buộc bởi task: **CHỈ nghiên cứu + đánh giá khả thi + ánh xạ + đề xuất phasing**. KHÔNG viết options,
> KHÔNG viết ADR, KHÔNG build. Options/ADR là việc của SA ở Gate 1 của CHG-002.
>
> Inputs đã đọc: `records/decisions.md` (CHG-001, CHG-002, D-001/D-002/D-003), `company-knowledge-mcp-spec.md`
> v1.0 (§13–14 context-pack, §24/§43 permission, §39 provenance, §40 hallucination control, §41 conflict),
> `1-discovery/options.md` (CHG-001 A/B/C, DP1–DP5), `2-gate1/plan-approval.md` (CHG-001 Option C approved),
> `4-design/architecture.md` (nền 9-nguồn: C1–C7, 5-lớp read-only, envelope, stdio transport-agnostic, mcp_ingest,
> pgvector), ADR-0002 (stdio transport-agnostic), 0003 (read-only defense-in-depth 5 lớp), 0010 (embedding
> local bge-m3 offline), 0011 (pgvector schema/upsert), 0016 (visibility/RBAC, kb team-only default-deny),
> 0017 (CHG-001 deviation), `knowledge/lessons.md` (L-001 choke point + adversarial, L-002 metric hai nghĩa,
> L-003 path-tolerant).

## Question(s) researched
1. Mỗi block trong 10 block của CEO: **đã có** trong nền hiện tại / **CHG-001 Option C sẽ làm** / **còn thiếu
   hoàn toàn**? (mục đích: không làm hai lần).
2. Các block còn thiếu khả thi tới đâu (độ khó, phụ thuộc, ràng buộc, bằng chứng từ landscape)?
3. Memory + State + Orchestrator mang **trạng thái GHI** — ghi vào store nội bộ (bảng mới trong Postgres `kb`)
   có phá bất biến read-only của 9 nguồn không? Orchestrator/Harness gọi LLM/agent thì còn là "read-only MCP"
   không? Giữ stdio NFR-005 thế nào khi thêm Orchestrator?
4. Grounding/Evidence "không evidence → UNKNOWN, không bịa fact" có enforce được như một **contract tại choke
   point** không (khớp L-001/L-002) + confidence score? Đây có phải bổ sung giá trị lớn nhất không?
5. Đề xuất phasing khả thi (có thể khác roadmap CEO nếu có lý do kỹ thuật).
6. Block nào cần ADR/options riêng (SA làm sau), block nào chỉ incremental trên CHG-001.

## Internal assets already available (tái dùng, tránh làm hai lần)
Nền 9-nguồn đã go-live local + CHG-001 Option C đã duyệt build cho rất nhiều hạ tầng mà CHG-002 sẽ dựa lên.
Mọi tham chiếu dưới đây là bằng chứng nội bộ (đọc trực tiếp từ repo), không cần web.

| Asset nội bộ | Ở đâu | CHG-002 dùng cho block nào |
|---|---|---|
| **Ingestion pipeline** (crawl→normalize→redact→chunk→embed→upsert idempotent, checkpoint, tombstone, `ingest_failures`) | `mcp-ingest` CLI, ADR-0012 | Block 1 (Ingestion) — gần như xong |
| **Embedding local bge-m3 1024d cosine, offline** (`HF_HUB_OFFLINE=1`) | ADR-0010 | Block 3 (RAG), giữ data-boundary cho mọi block |
| **Postgres + pgvector, một store** (HNSW cosine, role `mcp_query_ro` read-only, migration đánh số) | ADR-0011 | Block 2/3/5/6/7/8 — mọi store nội bộ dựa lên đây |
| **Envelope kết quả + Citation + Meta** (citation bắt buộc, `empty`/`not_found` là *status*) | ADR-0004 | Block 4 (Grounding), Block 7 (Provenance) — khung provenance đã có |
| **5-lớp read-only defense + transport assertion GET/HEAD + credential self-check lúc khởi động** | ADR-0003 (+A1/A2/A3) | Giữ bất biến read-only cho mọi block có "ghi store nội bộ" |
| **stdio transport-agnostic runtime** (`build_server/serve`, logic tool không biết transport) | ADR-0002 | Giữ NFR-005; nơi Orchestrator/gateway-boundary cắm vào |
| **Visibility + default-deny + team-only kb** | ADR-0016 | Block 8 (Governance) — điểm khởi đầu lifecycle |
| **Hybrid-RAG (tsvector+pgvector+RRF+reranker local+compression+context-pack)** — *CHG-001 Option C sẽ build* | options.md DP2/DP3, plan-approval | Block 3 (RAG) — đừng làm lại |
| **document_versions, entities/relationships (CTE), knowledge_summaries, document_permissions server-side** — *CHG-001 Option C sẽ build* | options.md DP1/DP4, plan-approval §"What is approved" | Block 2/7/8 một phần — đừng làm lại |
| **Jira nguồn thứ 10 (thin REST read-only)** — *CHG-001 Option C sẽ build* | options.md DP5, ADR-0007 | Block 1 (Ingestion) mở rộng |
| **E8 retrieval eval (Recall@K/MRR/NDCG) + MCP/RAG telemetry** — *CHG-001 Option C sẽ build* | D-001 khuyến nghị (c), options.md | Block 9/10 một phần — đừng làm lại |
| **Choke-point + adversarial-test discipline (L-001), metric-hai-nghĩa (L-002), path-tolerant (L-003)** | lessons.md | Enforce Grounding (B4), đo Evaluation (B9) đúng |

**Kết luận asset:** phần lớn **Block 1 + Block 3** đã có hoặc đang được CHG-001 làm. CHG-002 không được build lại
chúng; giá trị CHG-002 nằm ở các block CHG-001 **không** phủ (B4 đầy đủ, B5, B6, B7 đầy đủ, B8 lifecycle, B9/B10
đầy đủ, Orchestrator/Harness).

## Mapping — 10 blocks × (đã có / CHG-001 Option C / còn thiếu)
Legend: ✅ đã có ở nền hiện tại · 🟡 CHG-001 Option C sẽ làm (một phần) · 🔴 còn thiếu hoàn toàn (giá trị CHG-002).

| # | Block (CEO) | Đã có ở nền | CHG-001 Option C | Còn thiếu (CHG-002) | Gap chính |
|---|---|---|---|---|---|
| 1 | **Ingestion** | ✅ pipeline đầy đủ (9 nguồn, chunk/embed/upsert, tombstone) | 🟡 + Jira (#10) | — gần như đủ | Chỉ thêm nguồn (Slack/PDF/DB-spec) khi CEO cần; **incremental** |
| 2 | **Knowledge Model** (Company Knowledge Graph) | 🟡 pgvector doc store; chưa có entity graph | 🟡 `entities`/`relationships` (CTE 2–3 hop), `knowledge_summaries` | 🔴 **mô hình entity-type phong phú** (Company├Product/Service/Team/API/DB/Event/BusinessRule/ADR/Person-Owner) + extraction vào ingest | CHG-001 có *khung bảng*; CHG-002 cần *ontology + extraction* đầy đủ |
| 3 | **RAG / Retrieval** | ✅ semantic search đơn giản (pgvector) | 🟡 Hybrid đầy đủ (keyword+vector+metadata+RRF+reranker+compression+context-pack) | — đủ sau CHG-001 | Thêm "lọc theo entity/type/status/env **trước** semantic" cần Block 2+8 → **incremental sau B2/B8** |
| 4 | **Grounding / Evidence** | 🟡 citation bắt buộc + `not_found` status (envelope) | 🟡 provenance trong context-pack, conflict/authority (một phần §40/§41) | 🔴 **CONTRACT fact-or-UNKNOWN + confidence score + "không evidence → không company fact"** enforce tại choke point | Khung có; **quy tắc enforce + confidence + UNKNOWN** chưa là bất biến có test |
| 5 | **Memory** (AI đã học/quyết định gì) | ❌ không có | ❌ không trong CHG-001 | 🔴 **toàn bộ**: store Decision/Date/Reason/Status + TTL/version/status; đọc lại ở lần sau | Store GHI nội bộ mới (xem §Invariant check) |
| 6 | **State** (AI đang làm gì, tới đâu) | ❌ không có | ❌ không trong CHG-001 | 🔴 **toàn bộ**: task progress store | Store GHI nội bộ mới (xem §Invariant check) |
| 7 | **Provenance** (đến từ đâu + đổi thế nào) | 🟡 citation + `synced_at` (envelope §39) | 🟡 `document_versions` (lịch sử/rollback) → chain một phần | 🔴 **chain đầy đủ** fact←doc_version←ADR←approver←date + **obsolete propagation** (v2→v3 ⇒ fact cũ obsolete) | CHG-001 có *versioning*; CHG-002 cần *liên kết fact↔version + lan truyền obsolete* |
| 8 | **Governance** (lifecycle) | 🟡 visibility + default-deny (ADR-0016) | 🟡 permission server-side (document_permissions) | 🔴 **status lifecycle** Official/Draft/Deprecated/Experimental/Unknown + quy tắc "company fact chỉ dùng Official+Active" | Permission (ai thấy) ≠ lifecycle (fact nào đáng tin); cần field + filter mới |
| 9 | **Evaluation** (test AI như test software) | ❌ (NFR-003 UNVERIFIED, D-002) | 🟡 E8: retrieval eval Recall@K/MRR/NDCG + telemetry | 🔴 **~100 Company Questions (Q→expected) + gate "giảm chất lượng → không deploy"** | CHG-001 đo *retrieval metric*; CHG-002 cần *bộ câu hỏi công ty + regression gate end-to-end* |
| 10 | **Observability** (trace đầy đủ) | 🟡 structured log stderr, SLIs rollback (cab-pack) | 🟡 MCP/RAG telemetry (E8) | 🔴 **trace end-to-end** question→retrieval→docs→rerank→context→LLM→MCP→answer + đo grounding-failures/cost/token | CHG-001 có telemetry điểm; CHG-002 cần *trace xuyên tầng + grounding-failure metric* |
| — | **Orchestrator + Harness** (bọc Core) | ❌ không có | ❌ không trong CHG-001 | 🔴 **toàn bộ**: điều phối retrieval→grounding→memory/state→LLM; harness bọc ngoài | Có thể gọi LLM/agent → **tầng mới**, xem §Invariant check |

## Invariant check — Memory/State/Orchestrator mang trạng thái GHI (câu hỏi then chốt của CEO)

Đây là phần phân tích quan trọng nhất, vì B5/B6 và Orchestrator đảo một giả định có vẻ cốt lõi: "hệ thống
read-only". Phân tích tách bạch hai khái niệm bị gộp nhầm.

### (a) Ghi store NỘI BỘ ≠ ghi vào 9 NGUỒN — read-only invariant KHÔNG bị phá
- Bất biến read-only (BR-001/NFR-001/ADR-0003) được định nghĩa chính xác là: **không tool nào gây side effect
  GHI lên hệ nguồn** (Confluence/GitLab/Jira/OpenSearch/CloudWatch/Kafka/Redis/SQS-SNS/Postgres-nguồn). Nó
  enforce bằng 5 lớp: không đăng ký tool ghi · allowlist client · credential read-only ở nguồn · cấm side
  effect gián tiếp · test transport GET/HEAD + adversarial.
- **Nền hiện tại ĐÃ ghi vào một store nội bộ** mà không phá bất biến: `mcp-ingest` dùng role `mcp_ingest_rw`
  ghi vào schema `kb` (chunks/embeddings/versions). Đường ghi này **tách hoàn toàn** khỏi đường đọc của MCP
  server (role `mcp_query_ro`, `READ ONLY` tx) và **không bao giờ** ghi ngược ra nguồn. → Tiền lệ đã có: ghi
  vào `kb` nội bộ là hợp lệ, read-only nói về **nguồn**, không phải về **mọi** Postgres.
- **Kết luận:** Memory/State ghi vào **bảng mới trong `kb`** (vd `agent_memory`, `agent_state`) **KHÔNG phá**
  read-only của 9 nguồn, với điều kiện bắt buộc:
  1. Đường ghi Memory/State dùng **credential riêng** (không phải `mcp_query_ro`; cũng **không** nên tái dùng
     `mcp_ingest_rw` — tách principal để least-privilege), và **tuyệt đối không** có quyền ghi lên bất kỳ nguồn.
  2. Tool MCP mà Claude gọi để **đọc** Memory/State vẫn qua `mcp_query_ro` (read-only surface giữ nguyên 5 lớp).
  3. Việc **ghi** Memory/State phải là một **đường riêng** (giống `mcp-ingest`: CLI/orchestrator, không phải
     MCP tool read-only) HOẶC một tool ghi-nội-bộ **được tách khỏi tool surface read-only** và được audit —
     nếu đi đường tool, 5-lớp read-only assertion của ADR-0003 (áp cho 9 nguồn) **không** được nới; phải định
     nghĩa một surface riêng với invariant riêng "chỉ ghi `kb.agent_*`, không chạm nguồn".
- **Cảnh báo (L-001):** đây là bề mặt mới. Phải có **một choke point** cho mọi ghi nội bộ + **adversarial test**:
  "đường ghi Memory/State không thể ghi vào bảng nguồn / không thể phát request ra Confluence/Jira/…". Guarantee
  trong văn bản mà không có test = chưa enforce.

### (b) Orchestrator/Harness gọi LLM/agent → đây là MỘT TẦNG MỚI, không còn là "read-only MCP"
- MCP hiện tại là **access layer thụ động**: Claude (ở client) gọi tool, server trả dữ liệu. Không có vòng lặp
  agent phía server, không gọi LLM phía server.
- Orchestrator điều phối retrieval→grounding→memory/state→**LLM**→answer **là chủ động** và **có thể gọi LLM**.
  Hai hệ quả:
  1. **Nếu Orchestrator gọi LLM qua API ngoài (OpenAI/Anthropic/Bedrock)** → **egress + vendor trả phí** →
     **vi phạm `vendors=none`** và chính sách data-boundary "dữ liệu nội bộ không ra ngoài". **ĐÂY LÀ CẢNH BÁO
     LỚN NHẤT về vendor của CHG-002.** Cần CEO quyết (giống deviation +40 của reranker API trong ADR-0017).
     Phương án giữ baseline: LLM **local** (self-hosted) — nhưng egress HF đang 403, tải weights LLM lớn offline
     là một bài toán hạ tầng chưa chứng minh; hoặc Orchestrator **không gọi LLM** mà chỉ lắp context-pack rồi để
     Claude (client) tổng hợp (giữ mô hình hiện tại — khả thi nhất, xem §phasing).
  2. **Nếu Orchestrator là một service chạy nền / có vòng lặp** → giống Gateway service HTTP của CHG-001 Option
     B: **phá NFR-005 stdio-only + thêm SPOF**. Giữ NFR-005 ⇒ Orchestrator phải là **module in-process** trong
     runtime stdio hiện có (ADR-0002), **không mở cổng mạng** — y hệt cách CHG-001 Option C giải quyết gateway.
- **Kết luận Orchestrator:** khả thi mà **giữ cả read-only lẫn stdio** CHỈ KHI: (i) in-process (không service),
  (ii) tổng hợp để **Claude-ở-client** gọi LLM (không gọi LLM phía server) hoặc dùng LLM local offline, (iii) mọi
  ghi chỉ vào `kb.agent_*` qua choke point. Nếu CEO muốn Orchestrator **tự gọi LLM API** → đó là **deviation
  vendor + egress + có thể phá stdio**, phải escalate CEO ở Gate 1 CHG-002 (SA ra options/ADR).

### (c) Giữ stdio NFR-005 khi thêm Orchestrator
- Dùng đúng khuôn CHG-001 Option C đã được duyệt: thêm năng lực ở **tầng code in-process** của runtime
  transport-agnostic (ADR-0002), **không** service mạng. Khi v1.1 mở HTTP+SSE (BR-004/C4) chỉ đổi transport
  trước cùng module, không viết lại. → Orchestrator-as-module (không Orchestrator-as-service) là cách duy nhất
  giữ NFR-005.

### Tóm tắt cảnh báo bất biến
| Bất biến | Memory/State | Orchestrator/Harness | Điều kiện giữ |
|---|---|---|---|
| **Read-only 9 nguồn** (BR-001/NFR-001) | Giữ nếu ghi vào `kb.agent_*` bằng credential riêng, không chạm nguồn | Giữ nếu không bao giờ ghi ngược nguồn | choke point + adversarial test (L-001); principal tách |
| **stdio-only** (NFR-005) | Giữ (store trong Postgres, không service) | **Rủi ro phá** nếu là service → phải in-process | Orchestrator-as-module (ADR-0002), không mở cổng |
| **vendors=none / data-boundary** | Giữ (local Postgres) | **Rủi ro phá LỚN** nếu gọi LLM API ngoài | LLM local offline, hoặc để Claude-client tổng hợp (không gọi LLM server) |

## Grounding/Evidence như một CONTRACT enforce được (câu hỏi 4 của CEO)
**Đánh giá: KHẢ THI, và đây là bổ sung GIÁ TRỊ LỚN NHẤT của CHG-002** (trả lời trực tiếp mục tiêu CEO "không mơ
hồ/suy diễn thành fact"). Lý do khả thi cao:
- Nền đã có **đúng điểm tựa**: envelope bắt buộc Citation + `empty`/`not_found` là *status* (ADR-0004); spec
  §39 provenance, §40 hallucination control (citations/freshness/confidence/explicit not-found/conflict/version),
  §13–14 context-pack. Grounding contract là việc **siết các mảnh này thành một quy tắc enforce**, không phải
  xây từ 0.
- **Khớp L-001 (choke point + adversarial):** context-pack được lắp ở **đúng một chỗ** (retrieval→context
  assembly của Hybrid-RAG mà CHG-001 Option C xây). Đặt contract tại chính choke point đó:
  - Quy tắc: **mỗi claim trong context-pack phải mang {source, document_id, version, owner, updated, confidence,
    evidence_span}**; claim không có evidence hợp lệ → **bị loại hoặc đánh dấu UNKNOWN**, context-pack trả
    `status=insufficient_evidence` thay vì bịa.
  - Adversarial test (bắt buộc, kiểu E-001..E-003 đã dạy ở L-001): feed câu hỏi mà corpus **không** có evidence
    → assert output là UNKNOWN/insufficient, **không** có fact bịa; feed claim thiếu provenance → assert bị loại.
- **Khớp L-002 (metric hai nghĩa):** `confidence` phải được **định nghĩa đo cái gì** (vd retrieval similarity ×
  governance-status × freshness) và **không** được đọc như "độ đúng của fact". Nhãn rõ: confidence = độ mạnh
  evidence, không phải xác suất đúng. Tránh lặp lỗi E-004 (recall proxy đọc nhầm thành NFR).
- **Phụ thuộc:** contract đầy đủ cần **Block 7 (Provenance: version/owner/updated)** + **Block 8 (Governance:
  Official/Active)** để "company fact" có nghĩa chặt. Phiên bản tối thiểu (fact-or-UNKNOWN + citation +
  confidence) chạy được **ngay trên context-pack của CHG-001** mà chưa cần B7/B8 đầy đủ → có thể giao sớm.

## Feasibility per missing block
Thang độ khó: Low / Med / High. "Phụ thuộc" = block/asset phải có trước.

| Block thiếu | Độ khó | Phụ thuộc | Ràng buộc / rủi ro chính | Bằng chứng |
|---|---|---|---|---|
| **B4 Grounding/Evidence contract** | **Med** (giá trị cao nhất) | context-pack (CHG-001 E3); đầy đủ cần B7+B8 | Phải là choke point + adversarial test (L-001); confidence phải nhãn rõ (L-002) | nền ADR-0004 + spec §39/§40; mô hình RAG grounding/attribution là thực hành đã chín [1][2] |
| **B2 Knowledge Model đầy đủ** | **High** | entities/relationships CHG-001 (khung); ingest extraction | Entity extraction chính xác là bài toán NLP khó; sai → graph nhiễu. CTE 2–3 hop đủ cho nông, sâu cần đo | khung bảng + CTE đã có (options DP4); property-graph trên Postgres khả thi (recursive CTE / Apache AGE) [3][4] |
| **B5 Memory** | **Med** | Postgres store; principal ghi riêng | Store GHI nội bộ (xem §Invariant); schema Decision/Date/Reason/Status/TTL; dọn TTL/obsolete | Agent memory (episodic/semantic, TTL) là pattern đã thành hình trong agent frameworks [5][6] |
| **B6 State** | **Low–Med** | Postgres store; principal ghi riêng | Store GHI nội bộ; task-progress đơn giản hơn Memory | checkpoint/thread state là pattern chuẩn (LangGraph checkpointer, v.v.) [6] |
| **B7 Provenance đầy đủ** | **Med–High** | B2 (entity) + document_versions (CHG-001) | **Obsolete propagation** (v2→v3 ⇒ fact cũ obsolete) cần liên kết fact↔version — mô hình dữ liệu không tầm thường | versioning đã có (CHG-001); lineage/provenance model là thực hành đã biết (W3C PROV khái niệm) [7] |
| **B8 Governance lifecycle** | **Low–Med** | document_permissions/visibility (CHG-001/ADR-0016) | Thêm `status` (Official/Draft/Deprecated/Experimental/Unknown) + filter "company fact ⇒ Official+Active"; nhầm permission↔lifecycle là bẫy | ADR-0016 default-deny là nền; content-lifecycle là pattern CMS phổ biến |
| **B9 Evaluation đầy đủ** | **Med** | corpus thật + embedding đo được (gỡ egress HF) | NFR-003 vẫn UNVERIFIED (D-002/DK1); ~100 Company Q→expected cần người tạo; gate "giảm → không deploy" | RAG eval harness (golden set, regression gate) là thực hành chuẩn; Recall@K/MRR/NDCG đã trong E8 [8] |
| **B10 Observability đầy đủ** | **Low–Med** | telemetry E8 (CHG-001) | Trace xuyên tầng question→…→answer + grounding-failure/cost/token; nếu dùng OTel giữ in-process | structured log + SLIs đã có (cab-pack); tracing LLM/RAG (OTel GenAI) là pattern đã có [9] |
| **Orchestrator** | **High** | B4+B5+B6; quyết định LLM-ở-đâu | Xem §Invariant: in-process (giữ stdio) + không gọi LLM API ngoài (giữ vendors=none) — nếu không, deviation lớn | gateway in-process đã được duyệt (CHG-001 Option C) là tiền lệ kiến trúc |
| **Harness** | **Med** | Orchestrator | Bọc ngoài Core; chủ yếu là code tổ chức + eval hook; rủi ro thấp nếu không thêm service | — |

## Landscape (chỉ phần "build": thư viện / pattern / OSS cho block thiếu)
Theo mode (tier large → full), nhưng vendors=none nên **không** khảo giá SaaS; chỉ pattern & OSS tái dùng được
mà **giữ offline / in-process / stdio**.

| Giải pháp | Loại | Fit Must | Licence | Trưởng thành | Egress? | Nguồn |
|---|---|---|---|---|---|---|
| Postgres recursive CTE (đã dùng) | build-in | B2 graph nông, B7 chain | PostgreSQL (permissive) | rất chín | Không | [3] |
| Apache AGE (openCypher trên PG) | OSS extension | B2 traversal sâu | Apache-2.0 | trung bình; cần verify SPDX | Không | [4] |
| LangGraph checkpointer / state store | OSS pattern | B5/B6/Orchestrator | MIT | đang chín nhanh | Không nếu self-host | [6] |
| OpenTelemetry GenAI semconv | OSS chuẩn | B10 trace | Apache-2.0 | mới nhưng ổn | Không (collector local) | [9] |
| Ragas / eval harness pattern | OSS | B9 golden-set gate | Apache-2.0 | đang chín | **có thể egress nếu dùng LLM-judge** → tránh | [8] |
| bge-m3 (đang dùng) / cross-encoder local | model local | B3/B4 confidence | permissive | chín | Không (offline) | nội bộ ADR-0010 |
| LLM local self-host (vd llama.cpp/vLLM) cho Orchestrator | OSS runtime | Orchestrator gọi LLM mà không egress | permissive | chín | Không **nhưng** tải weights offline khó khi HF 403 | [10] |

**Cảnh báo copyleft/source-available:** không block nào bắt buộc GPL/AGPL/SSPL. LangGraph (MIT), AGE/OTel/Ragas
(Apache-2.0), Postgres (permissive) — đều an toàn. Mọi LLM-as-judge trong eval và mọi LLM API cho Orchestrator
là **egress + có thể vendor trả phí** → đánh dấu, cần CEO.

## Phasing khả thi đề xuất (có thể khác roadmap CEO — lý do kỹ thuật)
Roadmap CEO: P1 Ingestion/Store/RAG/MCP · P2 KnowledgeModel/Metadata/Provenance/Grounding · P3
Memory/State/Orchestrator/Harness · P4 Evaluation/Observability/Governance.

**Đề xuất điều chỉnh (giữ tinh thần CEO, đổi thứ tự theo phụ thuộc + giá trị sớm):**
- **P1 — đã/đang có:** Ingestion + RAG + MCP = nền hiện tại + CHG-001 Option C. **CHG-002 không làm lại.**
- **P2a (làm TRƯỚC, giá trị cao, phụ thuộc thấp): B4 Grounding/Evidence contract phiên bản tối thiểu**
  (fact-or-UNKNOWN + citation + confidence) **ngay trên context-pack CHG-001**, + **B8 Governance `status`**
  (vì "company fact = Official+Active" là input của contract). *Lý do: đây là mục tiêu cốt lõi của CEO và chạy
  được sớm mà không chờ Orchestrator.*
- **P2b: B2 Knowledge Model đầy đủ** (ontology + extraction) **rồi B7 Provenance đầy đủ** (chain + obsolete
  propagation). *Lý do kỹ thuật: Provenance chain cần entity + document_versions trước; B7 phụ thuộc B2 và
  versioning của CHG-001 → không thể làm B7 trước B2.* Nâng B4 lên "company fact" đầy đủ sau khi B7/B8 xong.
- **P3: B9 Evaluation (~100 Company Questions) + B10 Observability** — đặt **trước** Orchestrator, không phải
  sau. *Lý do: Orchestrator/Harness là tầng rủi ro cao; phải có bộ test công ty + trace để "giảm chất lượng →
  không deploy" (CEO yêu cầu) đo được Orchestrator ngay khi nó xuất hiện. Observability là tiền đề debug
  Orchestrator.*
- **P4: B5 Memory + B6 State + Orchestrator + Harness** (cuối). *Lý do: rủi ro bất biến cao nhất (ghi store,
  khả năng gọi LLM, khả năng phá stdio); cần B4/B9/B10 làm lưới an toàn trước. State (B6) dễ, có thể làm sớm
  độc lập nếu cần demo.*

**Khác biệt chính với CEO:** (1) đưa **Grounding (B4) + Governance-status (B8) lên sớm nhất** vì giá trị cao &
phụ thuộc thấp & đúng mục tiêu "không bịa fact"; (2) đưa **Evaluation + Observability TRƯỚC Orchestrator** để
có lưới an toàn; (3) **Memory/State/Orchestrator để cuối** vì tập trung mọi rủi ro bất biến. Giữ nguyên: B7
sau B2 (bắt buộc theo phụ thuộc).

## Block độc lập vs phụ thuộc (tóm tắt)
- **Độc lập, làm sớm được:** B4 (tối thiểu), B8, B6 (State), B10.
- **Phụ thuộc chuỗi:** B7 ⟵ B2 ⟵ (entities CHG-001); B4-đầy-đủ ⟵ B7+B8; Orchestrator ⟵ B4+B5+B6; B9 ⟵
  corpus+embedding đo được (gỡ egress HF, nối DK1).

## Block cần SA ra options/ADR riêng vs incremental trên CHG-001
| Block | SA cần options/ADR? | Lý do |
|---|---|---|
| B1 Ingestion (+nguồn mới) | **Incremental** | Theo khuôn connector hiện có; chỉ ADR nhỏ nếu nguồn lạ (Slack/DB-spec) |
| B2 Knowledge Model đầy đủ | **ADR + options** | Ontology + entity-extraction (rule vs model-based) + store (CTE vs AGE) — chạm "một store", có thể model egress |
| B3 RAG | **Incremental** | Đã là CHG-001; chỉ thêm "lọc entity/type/status/env trước semantic" |
| B4 Grounding/Evidence | **ADR** (contract) | Định nghĩa contract + confidence (L-002) + UNKNOWN semantics; là bất biến mới |
| B5 Memory | **ADR + options** | Store GHI nội bộ mới + principal/credential + schema + TTL/obsolete; chạm invariant (dù không phá) |
| B6 State | **ADR nhỏ** | Store GHI nội bộ nhưng đơn giản; chung ADR với B5 được |
| B7 Provenance đầy đủ | **ADR** | Mô hình fact↔version + obsolete propagation |
| B8 Governance lifecycle | **ADR nhỏ** | Thêm `status` + filter; chung hướng ADR-0016 |
| B9 Evaluation | **ADR** | Golden-set 100 Q + gate deploy + tránh LLM-judge egress (L-002) |
| B10 Observability | **Incremental/ADR nhỏ** | Mở rộng telemetry E8 + trace; ADR nếu dùng OTel collector |
| **Orchestrator** | **ADR + options (LỚN)** | LLM-ở-đâu (egress/vendor), in-process vs service (stdio), ghi store — gom nhiều deviation; **phải escalate CEO** |
| **Harness** | **cùng ADR Orchestrator** | Bọc ngoài Core |

## Build-vs-buy observations
- **Build (tái dùng nền) thắng áp đảo** cho B4/B6/B7/B8/B10: đều là code + bảng Postgres trên nền đã có, giữ
  offline/in-process/stdio, $0 run, vendors=none.
- **Buy/OSS cân nhắc** chỉ ở B2 (AGE nếu CTE chậm — SA đo) và Orchestrator (LangGraph pattern self-host — không
  bắt buộc, có thể tự viết in-process).
- **Vendor trả phí / egress** chỉ xuất hiện nếu: Orchestrator gọi LLM API ngoài, hoặc eval dùng LLM-judge, hoặc
  B2 extraction dùng LLM API. **Mọi trường hợp này = deviation, cần CEO** (như +40 reranker API ở ADR-0017).

## Sources
> Quy ước: mọi tuyên bố "pattern đã chín/khả thi" dựa trên landscape kỹ thuật phổ biến; các mục đánh dấu
> **unverified** chưa mở repo/trang để xác minh SPDX/phiên bản trong phiên này (time-box). Nền nội bộ (ADR-xxxx,
> spec, options.md) là bằng chứng trực tiếp đọc từ repo, không cần nguồn ngoài.

1. RAG grounding / attribution & "answer only from retrieved evidence" — thực hành phổ biến trong tài liệu RAG. **unverified** (không fetch web phiên này; nối bằng chứng nội bộ ADR-0004 + spec §40).
2. Confidence/relevance scoring trong retrieval (rerank score, similarity) — spec §40, options.md DP2. (nội bộ)
3. PostgreSQL recursive CTE cho graph traversal — options.md DP4 + ADR-0011 (nội bộ, đã dùng).
4. Apache AGE (openCypher trên Postgres), Apache-2.0 — options.md Option B [17 của options]; SPDX **unverified**.
5. Agent memory (episodic/semantic, TTL/status) — pattern agent framework. **unverified** (không fetch).
6. LangGraph checkpointer / state store (MIT) — pattern state/orchestration self-host. **unverified**.
7. Provenance/lineage model (khái niệm chain fact←version←approver) — W3C PROV khái niệm. **unverified**.
8. RAG eval harness (golden set, Recall@K/MRR/NDCG, regression gate); Ragas Apache-2.0 — E8 CHG-001 (nội bộ) + pattern. **unverified** phần Ragas.
9. OpenTelemetry GenAI semantic conventions (Apache-2.0) cho trace LLM/RAG. **unverified**.
10. LLM self-host runtime (llama.cpp/vLLM) — để Orchestrator không egress; tải weights offline bị cản bởi HF 403. **unverified**.

> Access date cho mọi mục: 2026-10-01. Giá cả: n/a (vendors=none, không khảo SaaS).

## Confidence & gaps
- **Cao (bằng chứng nội bộ trực tiếp):** mapping 10 block vs nền/CHG-001; phân tích invariant (ghi `kb` nội bộ
  không phá read-only — có tiền lệ `mcp_ingest_rw`; stdio giữ bằng in-process như Option C; Orchestrator gọi
  LLM API = egress/vendor); B4 là giá trị cao nhất & khả thi trên context-pack sẵn có.
- **Trung bình:** độ khó B2 (entity extraction) và B7 (obsolete propagation) — phụ thuộc chất lượng extraction,
  cần SA spike; CTE đủ sâu tới đâu **chưa đo** (nối "what would change our mind" của options.md).
- **Thấp / cần verify (gaps):**
  1. SPDX & maturity của AGE / LangGraph / OTel-GenAI / Ragas — **chưa mở repo** (time-box; SA verify trước chốt).
  2. **NFR-003 vẫn UNVERIFIED** (D-002/DK1): B9 Evaluation thật bị chặn tới khi gỡ egress HF + chốt ADR-0010.
  3. **LLM-ở-đâu cho Orchestrator** — câu hỏi mở lớn nhất; local-offline khả thi về nguyên tắc nhưng tải weights
     offline chưa chứng minh (HF 403). Cần CEO quyết hướng (local vs API-với-deviation vs không-gọi-LLM-server).
  4. Không fetch web phiên này (ưu tiên bằng chứng nội bộ, time-box); các mục landscape ngoài đánh dấu
     **unverified** — SA/researcher xác minh khi ra options.

```
HANDOFF
feature: mcp-data-platform
mode: full
status: done
artifacts: [market-research-chg002.md]
mapping_10_blocks: |
  1 Ingestion = ✅ nền + 🟡 Jira(CHG-001) — gần đủ, incremental
  2 Knowledge Model = 🟡 entities/CTE(CHG-001) — 🔴 ontology+extraction đầy đủ còn thiếu
  3 RAG = ✅ nền + 🟡 Hybrid(CHG-001) — đủ sau CHG-001
  4 Grounding/Evidence = 🟡 envelope/§40 — 🔴 CONTRACT fact-or-UNKNOWN+confidence còn thiếu (giá trị cao nhất)
  5 Memory = 🔴 thiếu hoàn toàn (store GHI nội bộ)
  6 State = 🔴 thiếu hoàn toàn (store GHI nội bộ)
  7 Provenance = 🟡 versioning(CHG-001) — 🔴 chain+obsolete propagation còn thiếu
  8 Governance = 🟡 permission/visibility — 🔴 status lifecycle Official/Active còn thiếu
  9 Evaluation = 🟡 Recall/MRR/NDCG+telemetry(CHG-001) — 🔴 100 Company Q + deploy-gate còn thiếu
  10 Observability = 🟡 log/SLI+telemetry — 🔴 trace end-to-end + grounding-failure còn thiếu
  Orchestrator+Harness = 🔴 thiếu hoàn toàn (tầng mới, rủi ro bất biến cao nhất)
top_value_blocks:
  - B4 Grounding/Evidence contract (fact-or-UNKNOWN + confidence) — đúng mục tiêu CEO "không bịa fact", khả thi ngay trên context-pack CHG-001, choke point+adversarial (L-001/L-002)
  - B7 Provenance đầy đủ (chain + obsolete propagation) — biến "fact cũ" thành obsolete tự động
  - B8 Governance lifecycle (Official/Active) — làm "company fact" có nghĩa chặt; phụ thuộc thấp
  - B9 Evaluation (100 Company Questions + deploy-gate) — hiện thực "test AI như test software" + unblock NFR-003
  - B2 Knowledge Model đầy đủ — đầu tư CEO coi đáng giá nhất; khó nhất (extraction)
invariant_warnings:
  - read-only: ghi Memory/State/agent vào kb.agent_* KHÔNG phá read-only 9 nguồn (tiền lệ mcp_ingest_rw) NẾU dùng credential riêng + choke point + adversarial test; đọc vẫn qua mcp_query_ro
  - stdio NFR-005: Orchestrator PHẢI in-process (như gateway Option C), KHÔNG service → nếu service thì phá stdio+SPOF
  - vendor/egress: Orchestrator gọi LLM API ngoài = egress + vendor trả phí = phá vendors=none → CẢNH BÁO LỚN NHẤT, cần CEO; giữ baseline = LLM local offline hoặc để Claude-client tổng hợp (không gọi LLM server). Eval LLM-judge & B2 LLM-extraction cũng egress nếu dùng API
  - HF egress 403: B9 (NFR-003 thật) + tải weights model/LLM local offline bị chặn tới khi gỡ (nối DK1/D-002)
phasing_proposal: |
  P1 (có/đang có): Ingestion+RAG+MCP = nền + CHG-001 — KHÔNG làm lại
  P2a (sớm, giá trị cao, phụ thuộc thấp): B4 Grounding tối thiểu + B8 Governance status
  P2b: B2 Knowledge Model đầy đủ → B7 Provenance đầy đủ (B7 phụ thuộc B2); nâng B4 lên "company fact"
  P3 (TRƯỚC Orchestrator): B9 Evaluation + B10 Observability = lưới an toàn
  P4 (cuối, rủi ro bất biến cao nhất): B5 Memory + B6 State + Orchestrator + Harness
  Khác CEO: đưa Grounding+Governance lên sớm nhất; Eval+Observability trước Orchestrator; Memory/State/Orchestrator để cuối. Giữ: B7 sau B2.
blocks_need_sa_options_adr:
  - B2 Knowledge Model (ontology + extraction rule-vs-model + store CTE-vs-AGE) — options+ADR
  - B4 Grounding/Evidence contract (contract + confidence semantics + UNKNOWN) — ADR
  - B5 Memory (store GHI nội bộ + principal + schema + TTL/obsolete) — options+ADR
  - B6 State — ADR nhỏ (có thể chung B5)
  - B7 Provenance (fact↔version + obsolete propagation) — ADR
  - B8 Governance lifecycle (status + filter) — ADR nhỏ (hướng ADR-0016)
  - B9 Evaluation (golden-set + deploy-gate, tránh LLM-judge egress) — ADR
  - Orchestrator+Harness (LLM-ở-đâu, in-process vs service, ghi store) — options+ADR LỚN, escalate CEO
  incremental trên CHG-001 (không cần options): B1 Ingestion, B3 RAG, B10 Observability
sources: 10 (nhiều mục unverified — không fetch web phiên này, ưu tiên bằng chứng nội bộ; SA verify SPDX trước chốt)
gaps:
  - SPDX/maturity AGE/LangGraph/OTel-GenAI/Ragas chưa mở repo (time-box)
  - NFR-003 UNVERIFIED tới khi gỡ egress HF + chốt ADR-0010 (B9 thật bị chặn)
  - LLM-ở-đâu cho Orchestrator là câu hỏi mở lớn nhất (local-offline khả thi nhưng tải weights offline chưa chứng minh do HF 403)
  - CTE đủ sâu tới đâu cho B2 chưa đo
```
