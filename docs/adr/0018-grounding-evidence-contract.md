# ADR-0018: Grounding/Evidence contract — "company fact phải có evidence, không evidence → UNKNOWN, không bịa"

**Date**: 2026-10-01
**Status**: **proposed** (giữ) — CTO xác nhận; **đưa accepted khi eval chốt ngưỡng FACT↔LOW_CONFIDENCE còn `TBD`**.
Design (2026-10-01) đã chốt *hình dạng* công thức confidence (deterministic: `retrieval × agreement × freshness`)
và bất biến `no-evidence ⇒ UNKNOWN` enforce **ngay** — xem Amendment D1 ở cuối. Chỉ **ngưỡng số** phân loại
FACT↔LOW_CONFIDENCE còn chờ đo trên golden-set thật (NFR-003 UNVERIFIED, chặn bởi egress HF — L-002/D-002), nên
ADR vẫn **proposed** chứ không accepted.
**Deciders**: SA (squad-sa) đề xuất; CTO quyết (scope hẹp, không chạm invariant mới ngoài những gì ADR-0017 đã mở); CEO lưu ý ở §"CEO note"

> Feature: `mcp-data-platform` (CHG-002 block **B4 Grounding/Evidence**, phiên bản tối thiểu).
> B4 được CEO chọn trong CHG-002 vì giá trị cao nhất ("AI hiểu công ty, không bịa fact"); các block khác
> vào `docs/squad/features/mcp-data-platform/records/backlog.md`.
> ADR này **siết** các mảnh đã có — envelope + citation (ADR-0004), provenance (spec §39), hallucination
> control (spec §40), conflict (spec §41), source authority (spec §42), context-pack (spec §13–14) — thành
> **một contract enforce được tại một choke point**. Không xây mới từ 0. Không cần LLM server, không egress.

## Context

CEO mục tiêu: trợ lý trả lời về công ty phải **chỉ nói những gì có nguồn chính thức**, và khi không có nguồn
thì nói thẳng "không biết" thay vì suy diễn thành fact. Nền đã có đúng điểm tựa nhưng **chưa phải một bất biến
có test**:

- Envelope bắt buộc `citations` khi `status ∈ {ok, partial}`; `empty`/`not_found` là *status*, không phải lỗi
  (ADR-0004). → có khung provenance, **nhưng chưa có quy tắc "không evidence hợp lệ ⇒ không được là company fact".**
- Spec §39 provenance, §40 hallucination control (citations/freshness/confidence/explicit not-found/source
  unavailable/conflict/version-awareness), §41 conflict ("không gộp im lặng"), §42 source authority
  ("configurable, không hardcode vào prompt"), §13–14 context-pack. → là **yêu cầu bằng văn xuôi**, chưa có
  một chỗ enforce + test đối kháng.
- Bài học bắt buộc:
  - **L-001** — một guarantee an toàn (ở đây: "không bịa fact") phải enforce tại **một choke point cấu trúc mọi
    đường đi đều phải qua**, kèm **adversarial test** feed đúng đầu vào mà guarantee tuyên bố chặn. Guarantee
    sống trong docstring/ADR = chưa enforce.
  - **L-002** — không để một con số (vd `confidence`) mang hai nghĩa; nếu là proxy thì nhãn đúng cái nó đo và
    đánh dấu NFR gần đó là UNVERIFIED, đừng để gate đọc proxy thành bằng chứng.

**Trạng thái build thực tế (đã kiểm):** code hiện có là nền 9-source read-only; `mcp_pgvector` chạy
**semantic search đơn giản** (`kb_semantic_search` trả envelope ADR-0004 + `citations`). Subsystem Hybrid-RAG +
**context-pack assembler** (CHG-001 Option C, epic E3: RRF + reranker local offline + context-compression +
context-pack) **đã được duyệt Gate 1 nhưng CHANG được viết thành module**. B4 phải định nghĩa choke point theo
module context-pack **sẽ** có; nếu B4 giao trước khi module đó tồn tại, nó cắm tạm vào ranh giới trả kết quả
của tool semantic-search hiện có (xem Decision §2).

## Decision

Grounding/Evidence là một **contract machine-checkable** cắm tại **một choke point server-side** trên đường
context-pack, với bảy điều khoản sau. Enforce ở **server**, **không** dựa Claude tự giác (khớp ADR-0015
"nội dung là dữ liệu, không phải chỉ thị" và spec §43 "không rely solely on Claude").

### 1. Định nghĩa "company fact"
Một **claim** = một mệnh đề nguyên tử về công ty do retrieval đề xuất đưa vào context-pack. Mỗi claim chỉ được
mang verdict `FACT` khi gắn được **evidence hợp lệ** gồm **đủ** các trường:

| Trường | Nguồn | Bắt buộc |
|---|---|---|
| `source` | spec §39 (`source`) | ✔ |
| `source_version` | spec §39 (`version`); nếu nguồn chưa versioned → `null` + hạ confidence (xem §3) | ✔ (giá trị có thể `null`) |
| `owner` | metadata document; nếu thiếu → `null` + nhãn `owner_unknown` | ✔ (giá trị có thể `null`) |
| `updated_time` | spec §39 (`source_updated_at`) + `synced_at` | ✔ |
| `confidence` | tính theo §3; **nhãn rõ: độ mạnh evidence, KHÔNG phải xác suất claim đúng** (L-002) | ✔ |
| `evidence` | link (`url`/`source_uri`) **và** định danh truy ngược được (`document_id` + `chunk_id`/locator) | ✔ |

Quy tắc bất biến: **claim thiếu bất kỳ trường bắt buộc nào (giá trị, không phải khoá `null`-được-phép) ⇒ không
được là `FACT`.** Claim không có một nguồn chính thức nào ⇒ verdict `UNKNOWN`, và câu trả lời cho người dùng là
cố định: **"Tôi không tìm thấy nguồn chính thức xác nhận thông tin này."** Server **không** sinh ra, suy diễn,
hay "điền vào chỗ trống" một claim không có evidence.

`evidence` phải truy ngược: client có thể từ `document_id` + `chunk_id` lấy lại đúng đoạn gốc qua
`kb_get_document` — evidence giả / không mở lại được coi như không có evidence.

### 2. Choke point DUY NHẤT (khớp L-001)
Contract enforce tại **đúng một hàm**: **context-pack assembler**, chạy **sau rerank + context-compression,
ngay trước khi context-pack được trả về client**. Đây là điểm cuối cùng mọi claim phải đi qua trước khi rời
server. Thứ tự đường đi (CHG-001 Option C):

```
query → hybrid retrieve (vector+keyword+metadata+RRF)
      → rerank (cross-encoder local offline)
      → context-compression
      → [ GROUNDING GATE ]  ← choke point B4, server-side, DUY NHẤT
      → context-pack (envelope + provenance + grounding verdict)  → client (Claude)
```

- Grounding gate là **điểm bắt buộc đi qua**: không có đường vòng nào đẩy claim ra context-pack mà bỏ qua gate.
  Compression (spec §13) **không bao giờ** được nén mất provenance/evidence trước gate ("Never compress away
  provenance").
- Enforce **server-side**. Claude **không** được tin để tự lọc fact-không-nguồn; verdict do server đóng dấu.
- **Nếu module context-pack của CHG-001 chưa tồn tại khi B4 giao:** gate cắm tạm vào **ranh giới trả kết quả
  của tool semantic-search** (`mcp_pgvector` → mapper envelope ADR-0004, điểm mọi tool-result đi qua trước khi
  về client). Trong trạng thái tạm này B4 chỉ có input semantic-đơn (chưa có rerank/RRF/compression) nên
  confidence chỉ dùng được thành phần retrieval (xem §3) và verdict vẫn đúng ngữ nghĩa FACT/UNKNOWN/CONFLICT.
  Khi context-pack assembler ra đời, gate **di chuyển vào đó** và **đây vẫn là một chỗ duy nhất** — không được
  để tồn tại song song hai gate. Việc di chuyển ghi trong implementation-plan của B4; test đối kháng (§6) đi
  theo gate.

### 3. Confidence score — đo cái gì, ngưỡng phân loại
`confidence` = **độ mạnh của evidence đứng sau claim**, nhãn tường minh như vậy trong contract (L-002). Nó
**KHÔNG** phải xác suất claim đúng và **không gate nào được đọc nó như "độ đúng của fact"**.

Thành phần đầu vào (deterministic, không gọi LLM):
- **retrieval strength** — điểm sau rerank (hoặc similarity khi chưa có rerank), chuẩn hoá [0,1];
- **source agreement** — số nguồn độc lập cùng khẳng định claim (1 nguồn vs nhiều nguồn đồng thuận);
- **freshness** — độ tuổi của `updated_time` so với ngưỡng tươi của loại fact đó;
- (khi B7/B8 có) governance status (Official/Active) và version-currency — hiện **chưa có** nên không đưa vào
  công thức tối thiểu, ghi là mở rộng tương lai.

**Công thức tổng hợp cụ thể và các NGƯỠNG phân loại = `TBD cho design/eval`.** Lý do để TBD thay vì bịa số
(khớp L-002 và E-004): các ngưỡng (vd FACT ≥ x, LOW-CONFIDENCE trong [y, x), UNKNOWN < y) chỉ có nghĩa khi đo
trên **golden-set công ty thật**; mà corpus thật + embedding đo được còn chặn bởi egress HF (NFR-003 vẫn
UNVERIFIED — D-002/DK1). Design/eval của B4 **phải** chốt công thức + ngưỡng bằng dữ liệu đo và ghi lại; cho tới
lúc đó không được khẳng định một ngưỡng cứng là "đúng".

Bất biến độc lập với ngưỡng (đúng ngay cả khi ngưỡng chưa chốt): **claim không có evidence hợp lệ ⇒ UNKNOWN,
bất kể confidence**. Confidence chỉ phân biệt FACT vs LOW-CONFIDENCE **trong số các claim đã có evidence**;
nó không bao giờ "cứu" một claim không nguồn thành fact.

Verdict xuất ra: `FACT` | `LOW_CONFIDENCE` | `UNKNOWN` | `CONFLICT` (xem §4, §5).

### 4. Conflict handling (spec §41)
Khi ≥2 nguồn mâu thuẫn về cùng một claim: **không gộp im lặng, không tự chọn một bên**. Gate phát verdict
`CONFLICT` và **phơi bày cả hai (hoặc tất cả)** phía trong context-pack, mỗi phía mang evidence đầy đủ của nó
(§1) + `source_version` + `updated_time`. Thứ tự ưu tiên hiển thị lấy từ **source authority config**
(spec §42): priority theo **loại fact** (vd runtime/config → GitLab/deployment config; architecture →
Confluence; current work status → Jira; code behavior → GitLab), **configurable, KHÔNG hardcode vào prompt
LLM**. Server chỉ **chú thích** nguồn nào mới hơn / có thẩm quyền hơn theo config; **quyết định cuối để client
(Claude) giải thích cho người dùng**, không ép server chọn thắng-thua.

### 5. Hình dạng output (envelope + provenance + verdict)
Context-pack trả về mở rộng envelope ADR-0004, thêm, **cho từng claim**, trường `provenance` (các trường §1) và
`grounding` verdict. Hình dạng (sẽ chốt chính xác trong `api-contract.yaml` ở mode design, giữ tương thích
ADR-0004):

```json
{
  "status": "ok | empty | not_found | partial | insufficient_evidence",
  "grounding_summary": { "fact": 3, "low_confidence": 1, "unknown": 2, "conflict": 1 },
  "claims": [
    {
      "text": "Connection timeout là 3 giây.",
      "grounding": "FACT",
      "confidence": 0.0,
      "confidence_basis": "evidence-strength (retrieval×agreement×freshness), NOT P(claim true)",
      "provenance": [
        { "source": "confluence", "source_version": "12", "owner": "payments-team",
          "updated_time": "2026-09-30T03:15:00Z", "synced_at": "...",
          "evidence": { "url": "...", "document_id": "123", "chunk_id": "123#4" } }
      ]
    },
    {
      "text": "Read timeout là ... giây.",
      "grounding": "CONFLICT",
      "positions": [
        { "value": "5s", "provenance": [ { "source": "confluence", "source_version": "12", "...": "..." } ] },
        { "value": "10s", "provenance": [ { "source": "gitlab", "source_version": "abc123", "...": "..." } ] }
      ],
      "authority_note": "source_authority(runtime/config)=gitlab > confluence; gitlab mới hơn"
    }
  ],
  "unknowns": [
    { "asked": "SLA uptime của payment-service?",
      "grounding": "UNKNOWN",
      "message": "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này." }
  ]
}
```

- `status=insufficient_evidence` (mới, nối tiếp tinh thần `empty`/`not_found` của ADR-0004): truy vấn hợp lệ
  nhưng **không claim nào đạt FACT** → client phải trình bày UNKNOWN, không bịa.
- Client (Claude) dùng `grounding` + `provenance` để hiển thị **trung thực**: FACT có trích dẫn; UNKNOWN nói
  thẳng không biết; CONFLICT phơi bày các phía. Server **ép** hình dạng này; sự trung thực không phụ thuộc
  Claude tự giác (ADR-0015).

### 6. Testability — test contract cho QA (adversarial, khớp L-001)
B4 **chưa enforce** nếu chưa có bộ test đối kháng feed đúng đầu vào mà contract tuyên bố chặn. Contract test
(QA viết; chạy ở cùng cấp với bộ L-001 E-001..E-003):

- **GT-1 không-nguồn → UNKNOWN:** hỏi điều corpus **không** có evidence ⇒ assert verdict `UNKNOWN` /
  `status=insufficient_evidence`, assert **không** có claim `FACT`, assert message cố định xuất hiện. (Chống bịa.)
- **GT-2 có-nguồn → có evidence:** hỏi điều corpus **có** ⇒ assert claim `FACT` mang **đủ** 6 trường §1 và
  `evidence` **mở lại được** (resolve `document_id`+`chunk_id` qua `kb_get_document` ra đúng đoạn).
- **GT-3 provenance thiếu → bị hạ cấp:** đưa claim giả thiếu `evidence`/`updated_time` ⇒ assert **không bao giờ**
  thành `FACT` (thành `UNKNOWN`/`LOW_CONFIDENCE`), **không** bị gate bỏ qua.
- **GT-4 mâu thuẫn → phơi bày:** hai nguồn khác giá trị cùng claim ⇒ assert verdict `CONFLICT`, assert **cả hai**
  position + provenance xuất hiện, assert **không** gộp/chọn im lặng; `authority_note` theo config.
- **GT-5 một choke point:** test cấu trúc assert **mọi** đường ra context-pack đi qua grounding gate (không có
  claim nào tới output mà chưa được gate đóng verdict) — kiểu test "single choke point" của L-001.
- **GT-6 confidence là proxy:** assert field `confidence` luôn kèm `confidence_basis` nhãn evidence-strength, và
  assert **không** có đường nào để confidence cao biến claim-không-evidence thành FACT (bất biến §3).
- **GT-7 compression giữ provenance:** assert sau context-compression, claim vẫn còn đủ provenance trước khi
  vào gate (spec §13 "never compress away provenance").

QA ghi các GT-x này thành test-cases trong mode design/plan; ngưỡng số (§3) để `TBD` tới khi eval chốt — các
assert FACT/UNKNOWN/CONFLICT ở trên **không** phụ thuộc ngưỡng số nên chạy được ngay.

### 7. Bất biến giữ nguyên
- **Read-only tuyệt đối** (BR-001/NFR-001, ADR-0003): B4 chỉ đọc + đóng dấu verdict, không ghi nguồn.
- **stdio-only** (NFR-005, ADR-0002): gate là module in-process, **không** mở cổng mạng.
- **vendors=none / không egress** (ADR-0017): confidence + verdict **deterministic**, **không** gọi LLM/API
  ngoài, **không** embedding/rerank online. Reranker/embedding dùng lại model local offline đã duyệt
  (ADR-0010, `HF_HUB_OFFLINE=1`).
- **Permission server-side trước context assembly** (spec §24/§43, ADR-0016): permission filter
  (`enforce_permission`, default-deny) chạy **trước** grounding gate và là **MỘT choke point duy nhất cho CẢ 8
  tool nội dung** của tầng Knowledge — `search_company_knowledge`, `get_jira_context`, `search_code`,
  `get_service`, `get_repository`, `find_related_knowledge`, `get_knowledge_summary`, `get_document_version`
  (không chỉ 2 tool dựng context-pack). Mỗi tool chạy đúng một `enforce_permission` (một query
  `document_grants`) trước khi trả bất kỳ content/`source_uri`/provenance nào — tài liệu không-quyền không được
  là evidence/kết quả cho caller không quyền. **Corpus team-only** (ADR-0016 A1) là **phòng thủ nhiều lớp
  (defence-in-depth)** đứng **sau** choke point này, **KHÔNG** phải rào cản duy nhất ngăn rò rỉ. (Khớp code sau
  R-C-001: `records/errors.md` E-mcp-data-platform-007.)
- **Phụ thuộc mới cần thêm: KHÔNG.** B4 tối thiểu chạy trên context-pack (hoặc tool semantic-search tạm) + các
  bảng Postgres đã có; không datastore mới, không extension, không service. Nếu design phát hiện cần một phụ
  thuộc mới → phải quay lại ADR riêng + CTO (không âm thầm thêm).

## Alternatives Considered

### Alternative 1 (nga rẽ thiết kế chính): Trả raw provenance cho client, để Claude tự quyết fact-or-UNKNOWN
- **Pros**: Server đơn giản nhất; không cần đóng dấu verdict; linh hoạt cho client.
- **Cons**: "Không bịa fact" trở thành **lời hứa dựa vào Claude tự giác** — đúng loại guarantee "sống trong
  prose, không centralise, không adversarial test" mà **L-001** cấm; mâu thuẫn **ADR-0015** ("nội dung là dữ
  liệu, không chỉ thị") và **spec §43** ("không rely solely on Claude"). Không chỗ nào enforce ⇒ không test được.
- **Why not**: Bị L-001 + ADR-0015 + §43 loại thẳng. Đây là lý do **không cần options.md riêng**: nga rẽ lớn
  duy nhất đã bị các bất biến đã-chốt quyết định — enforce server-side là bắt buộc, không phải lựa chọn mở.

### Alternative 2: Dùng LLM-judge để chấm "claim có grounded không"
- **Pros**: Bắt được grounding tinh vi hơn heuristic.
- **Cons**: Thêm phụ thuộc LLM trong mọi context-pack = **egress + vendor** (phá ADR-0017), độ trễ, chi phí,
  và lại một judge không tất định. Trùng vết Alternative 2 của ADR-0015.
- **Why not**: Phá vendors=none/no-egress; không tương xứng cho B4 tối thiểu. Deterministic gate đủ cho mục
  tiêu "không bịa".

### Alternative 3: Nhiều điểm enforce (ở retrieval, ở compression, ở tool mỗi server)
- **Pros**: "Phòng thủ nhiều lớp".
- **Cons**: Nhiều gate = không gate nào là nguồn sự thật; dễ lệch nhau; đúng anti-pattern **L-001** cảnh báo
  (guarantee phân tán). Khó test "mọi đường đều qua".
- **Why not**: L-001 đòi **một** choke point cấu trúc. Permission filter (§7) là một gate *khác mục đích* chạy
  *trước*, không phải nhân bản grounding gate.

### Alternative 4: Chốt ngay một ngưỡng confidence cứng (vd FACT ≥ 0.7)
- **Pros**: Contract "đầy đủ" ngay, QA có số để assert.
- **Cons**: Con số bịa khi chưa đo trên golden-set thật = đúng lỗi **E-004/L-002** (số không đo được bị đọc như
  bằng chứng). NFR-003 còn UNVERIFIED (egress HF).
- **Why not**: Để `TBD cho design/eval` trung thực hơn; bất biến "không-evidence ⇒ UNKNOWN" đã đủ chặn bịa mà
  không cần ngưỡng.

## Consequences

### Positive
- "Không bịa fact" trở thành **bất biến có test** tại một chỗ, không còn là lời hứa văn xuôi (đóng khoảng trống
  🔴 mà research B4 nêu).
- Tái dùng tối đa nền đã có (envelope ADR-0004, provenance §39, context-pack §13–14); không phụ thuộc mới,
  không egress, giữ stdio + vendors=none.
- QA có test contract rõ (GT-1..GT-7) để viết ngay; FACT/UNKNOWN/CONFLICT chạy được không chờ ngưỡng.
- UNKNOWN tường minh + CONFLICT phơi bày cải thiện lòng tin của người dùng vào trợ lý.

### Negative
- Mỗi claim mang provenance + verdict ⇒ context-pack dài hơn, thêm token (giảm thiểu: compression giữ
  provenance nhưng nén phần văn bản; `grounding_summary` cho cái nhìn gọn).
- Trích tách "claim" nguyên tử từ chunk là bước xử lý mới; phiên bản tối thiểu có thể coi mỗi chunk-đáng-tin là
  một claim rồi tinh hoá sau (ghi trong design).
- Công thức + ngưỡng confidence còn `TBD` ⇒ phân biệt FACT vs LOW_CONFIDENCE chưa định lượng được tới khi eval
  chốt (đã nêu là residual, nối NFR-003/D-002).

### Risks
- **Choke point đôi khi di chuyển** (tool semantic-search tạm → context-pack assembler): rủi ro tồn tại hai
  gate song song. Giảm thiểu: GT-5 assert "một chỗ duy nhất"; implementation-plan ghi rõ việc dời.
- **Ngưỡng confidence chốt sai khi eval** nếu golden-set nhỏ/thiên lệch: giảm thiểu bằng để bất biến
  không-evidence⇒UNKNOWN độc lập ngưỡng, và gate eval "giảm → không deploy".
- **Claim-extraction sai** gán nhầm evidence cho mệnh đề: giảm thiểu bằng GT-2 (evidence phải mở lại đúng đoạn)
  và GT-3 (thiếu provenance ⇒ không FACT).
- Grounding **giảm thiểu, không triệt tiêu** bịa (giống injection ở ADR-0015): một claim có evidence vẫn có thể
  bị người đọc hiểu sai; contract chỉ đảm bảo *không có fact không nguồn rời server*.

## CEO note / deviation
- **Scope hẹp, không mở invariant mới ngoài ADR-0017.** B4 nằm trong deviation CHG-001 đã được CEO duyệt Gate 1
  (context-pack + permission server-side). Không thêm vendor/egress/service/datastore ⇒ **không cần Gate CEO
  mới**; CTO quyết trong scope.
- **Một điểm CEO nên biết:** ngưỡng phân loại confidence để **TBD cho design/eval** một cách có chủ đích (tránh
  lỗi E-004/L-002). Bất biến "không nguồn ⇒ UNKNOWN, không bịa" **đã** enforce được ngay; chỉ ranh giới
  FACT↔LOW_CONFIDENCE chờ đo trên corpus thật (còn chặn bởi egress HF — NFR-003 UNVERIFIED, D-002/DK1).
- **Phụ thuộc:** bản "company fact" đầy đủ (version/owner/updated chặt + lifecycle Official/Active) cần B7
  (Provenance) + B8 (Governance) trong backlog; bản tối thiểu này cố ý chạy trước và nâng cấp sau.

## Links
- Siết: ADR-0004 (envelope/citation), ADR-0015 (untrusted content, "data không phải chỉ thị"),
  ADR-0016 (visibility/permission), ADR-0017 (CHG-001 deviation — scope mở sẵn), ADR-0010 (embedding/rerank local).
- Spec: §13–14 (context-pack), §24/§43 (permission server-side), §39 (provenance), §40 (hallucination control),
  §41 (conflict), §42 (source authority).
- Lessons: L-001 (choke point + adversarial), L-002 (metric hai nghĩa).
- Research: `docs/squad/features/mcp-data-platform/1-discovery/market-research-chg002.md` §"Grounding/Evidence
  như một CONTRACT enforce được".

## Amendments (mode design CHG-001 Option C, 2026-10-01)

### D1 — Công thức confidence (deterministic) chốt hình dạng; chỉ ngưỡng còn TBD
Design chốt confidence là **tích chuẩn hoá của ba thừa số deterministic** (không gọi LLM, không egress), nhãn
đúng theo L-002 là **evidence-strength**, KHÔNG phải P(claim đúng):

```
confidence = w_r · retrieval  +  w_a · agreement  +  w_f · freshness        (dạng tổng-trọng-số)
             — HOẶC dạng tích để một thừa số = 0 kéo cả điểm xuống —
confidence = retrieval^{w_r} · agreement^{w_a} · freshness^{w_f}            (dạng hình học, mặc định)
```
- **retrieval** ∈ [0,1] — điểm sau rerank (cross-encoder local offline), chuẩn hoá min-max trên tập ứng viên;
  khi chưa có rerank (gate tạm ở ranh giới semantic-search) = cosine similarity chuẩn hoá.
- **agreement** ∈ [0,1] — `1 − 1/(1 + n_independent)` với `n_independent` = số **nguồn độc lập** (khác
  `source_type` hoặc khác document) cùng khẳng định claim; 1 nguồn → ~0.5, nhiều nguồn đồng thuận → →1.
- **freshness** ∈ [0,1] — hàm giảm theo tuổi của `updated_time` so với `freshness_horizon` của **loại fact**
  (config theo source-authority §4); quá hạn → tiệm cận 0 nhưng **không** 0 tuyệt đối (fact cũ vẫn là fact nếu
  không bị mâu thuẫn).
- **Trọng số mặc định** `w_r=0.5, w_a=0.3, w_f=0.2` (dạng hình học: số mũ) — **giá trị khởi điểm, calibrate ở
  eval**; ghi trong config `grounding.confidence.weights`, không hardcode vào prompt.
- Dạng **hình học là mặc định** vì nó cho một thừa số rất yếu (vd freshness≈0 trên fact đã quá hạn xa) kéo điểm
  xuống đúng trực giác; dạng tổng-trọng-số là tuỳ chọn config khi eval cho thấy phù hợp hơn.

**Ngưỡng phân loại — còn `TBD cho eval` (L-002/E-004):** `FACT ⇔ confidence ≥ τ_fact`,
`LOW_CONFIDENCE ⇔ τ_low ≤ confidence < τ_fact`, **trong số các claim ĐÃ có evidence hợp lệ**. `τ_fact`, `τ_low`
chỉ chốt khi đo được trên golden-set công ty thật (chặn bởi egress HF — NFR-003 UNVERIFIED). Khởi điểm để test
hạ tầng chạy (KHÔNG phải giá trị đã kiểm chứng): `τ_fact=0.6`, `τ_low=0.3`, đánh dấu `calibration_status:
uncalibrated` trong envelope cho tới khi eval chốt.

**Bất biến enforce NGAY (độc lập ngưỡng — không chờ eval):**
1. **no-evidence ⇒ UNKNOWN** bất kể confidence (claim không có evidence hợp lệ không bao giờ thành FACT/LOW).
2. **confidence không bao giờ "cứu" claim không nguồn** — gate không đọc confidence trước khi đã xác nhận evidence hợp lệ (thứ tự: evidence-check → mới tính confidence).
3. **≥2 nguồn mâu thuẫn ⇒ CONFLICT**, phơi bày mọi phía (không gộp, không chọn im lặng).
4. **confidence luôn kèm `confidence_basis` + `calibration_status`** để không gate/đọc nào hiểu nhầm là xác suất đúng.

Các bất biến 1–4 là thứ api-contract.yaml encode và GT-1..GT-7 (QA) assert được **ngay bây giờ**; chỉ phân biệt
FACT vs LOW_CONFIDENCE phụ thuộc ngưỡng và được đánh dấu `uncalibrated` tới khi eval chốt. Khi ngưỡng chốt →
ADR-0018 chuyển **accepted** và ghi giá trị τ đo được.
