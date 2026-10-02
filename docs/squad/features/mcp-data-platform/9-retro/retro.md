# mcp-data-platform — Retrospective (đóng feature, 2026-10-02)

> Retro blameless khi feature **đóng**: CHG-003 (real ingestion + real egress, Confluence Cloud
> trước) đã go-live và **cửa sổ theo dõi 30 phút đóng ở trạng thái XANH, không rollback**. CEO chọn
> đóng feature bây giờ và giữ NFR-003 ở mức **"đã đo lần đầu (cỡ mẫu nhỏ)"**.
>
> Dữ kiện lấy từ `state.json` (toàn bộ history, gồm hai mốc chg003-watch cuối: NFR-003 đo lần đầu
> 2026-10-02T22:18 và watch-close GREEN 22:25), `records/decisions.md` (D-001..D-008),
> `records/errors.md` (E-001..E-009), `7-release/release-log.md`, `docs/squad/knowledge/lessons.md`.
> Track: large. "Process, không phải người" xuyên suốt. (Tiêu đề mục giữ tiếng Anh theo chuẩn
> check.sh; văn xuôi cho CEO bằng tiếng Việt.)

## Outcome
- **Feature đóng sau go-live thật.** Nền 9-nguồn read-only đã live local từ Oct-1 (D-003). Vòng 2:
  **CHG-003** mở egress thật tới `*.atlassian.net` (Confluence Cloud `tnexwm.atlassian.net`) + tải
  model nhúng thật từ `huggingface.co`; ingest thật đầu tiên bounded vào **space EA** (+1 doc /
  +25 chunk; tổng 3 doc / 27 chunk). Verify qua stdio: `kb_semantic_search` trả chunk EA thật, trích
  dẫn `tnexwm.atlassian.net`. **Cửa sổ theo dõi 30′ đóng XANH, 0 rollback, 0 incident.**
- **CHG-001 Company Knowledge** (Gateway in-process + Hybrid-RAG + Jira + grounding + permission choke
  point) đã build xong + review APPROVE, nhưng CEO **HOÃN go-live** (artifact giữ nguyên).
- **CHG-002 B4 Grounding/Evidence contract** ("không evidence → không phải company fact", trả UNKNOWN,
  không bịa) ship **trong scope CHG-001** (CTO D-004). Các block còn lại → BACKLOG (`records/backlog.md`).
- **NFR-003 đo lần đầu bằng model thật** (`BAAI/bge-m3`, 1024-d, nạp offline): re-embed 27/27 chunk;
  trên 1 tài liệu EA (25 chunk) → **hit@5 = 8/8, MRR = 1.0, calibration_status=uncalibrated**. Trung
  thực (L-002): **đo THẬT nhưng CỠ MẪU NHỎ** — chưa suy rộng ra population recall. Theo Option 1 của
  CEO, NFR-003 giữ ở mức "đã đo lần đầu (cỡ mẫu nhỏ)", chưa phải "verified trên dữ liệu công ty".
- So với baseline: giảm scope 0%; cost/schedule trong envelope; CHG-003 run ≈ $0/tháng (tenant
  Atlassian sẵn có, model local offline).

### Hai cổng CEO + change CHG-003 — dòng thời gian
- **Gate 1 (CHG-003), 10:09 — DUYỆT Option B.** Mở egress `*.atlassian.net` (Atlassian = vendor,
  token read-only) + `huggingface.co` (tải model → gỡ chặn đo NFR-003). Egress chỉ cho ingest-pull +
  tải model; 9 server vẫn read-only + stdio. Track lean (CTO D-006: deviation ~86/100, rule 1+3 fire).
- **Build (10:18 → 11:40).** ADR-0023 (egress default-deny allow-list, một choke point `check_egress`)
  → FR-023..027 → T-111..123 → TC-111..131 → backend CE1..CE5. make ci GREEN 2344/0, 4 test §6e giữ.
  Review APPROVE round 1 (1 medium + 1 low non-blocking).
- **Gate 2 (CHG-003), 14:57 — DUYỆT go-live (Option 1).** `cab-approval.md` status approved (thỏa
  deploy-guard DK3). DK1 chấp nhận ship v1 với NFR-003 chưa chứng minh. Token read-only tại
  `./.token-key` (git-ignored; không echo giá trị).
- **Deploy (15:09 → 15:50).** Lần 1 CHẶN Step 1: `doctor` từ chối token write-capable (cổng read-only
  làm đúng). CEO chọn escape-hatch `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (CTO D-008: bề mặt vẫn 0
  write tool). Egress thật đầu tiên (space discovery, 100 space). CEO chọn scope **EA**; DK5 snapshot
  trước ghi; dry-run 0 write → pull thật +1 doc/+25 chunk → verify stdio PASS → smoke PASS → mở watch.
- **Watch (15:48 → đóng 22:25 GREEN).** Trong cửa sổ, CEO yêu cầu đo NFR-003 ngay: tải xong bge-m3,
  DK5 snapshot embeddings, re-embed thật, đo lần đầu. 5 trigger rollback đều XANH suốt → đóng GREEN.

## Flow metrics
- **Loops:** `review=2` (base round 1→2 + CHG-001 round 1→2; CHG-003 approve round 1), `spec=1`,
  `qa=0`. Mỗi vòng review đóng trong ≤ 2 round; không vòng lặp vô hạn.
- **Escalations tới CEO:** 3 — CHG-001 size LARGE (D-001, Option A), CHG-003 egress+vendor (D-006,
  Option B), và quyết escape-hatch token write-capable (D-008, CTO quyết trong scope Gate-2).
- **Decisions ledger:** D-001..D-008 (append-only). santa-method cab-readiness D-002 + D-007, mỗi lần
  2 checker PASS.
- **Change records:** CHG-001 (postponed), CHG-002/B4 (shipped in-scope), CHG-003 (live).
- **Interruptions (base):** 3 user-stop + 1 SA rate-limit crash, mỗi lần resume audit-first, không hỏng state.
- **Lead time (vòng 2):** CHG-003 Gate-1 2026-10-02T10:09 → go-live 15:50 → watch-close GREEN 22:25.

## Token use
- **Không có số token per-stage cho feature này:** `TOKEN_BUDGET_M=0` trong `.kiro/squad/config.env`
  và không có log `ecc:cost-tracking`, nên `state.json.history` không mang số token. Không dựng bảng
  token để tránh bịa số.
- **Tín hiệu định tính:** đắt nhất theo số lần dispatch là stage **backend** (base 5 batch + 2
  resume-audit; CHG-001 6 batch; CHG-003 3 batch) và **SA** (3 run ở base + reconcile). Review rẻ (fix
  song song disjoint, mỗi vòng ≤ 2 round).
- Follow-up: đặt `TOKEN_BUDGET_M` thật + wire `ecc:cost-tracking` trước khi mở lại CHG-001 để retro
  sau báo được spend/stage.

## Quality metrics
- **Defect theo môi trường:** dev/review có defect; **escaped ra live env = 0** (cả hai vòng); 0
  rollback, 0 incident. UAT/PRE = n/a theo thiết kế (per-user stdio; PRE-equivalent = host dev thật +
  Docker, D-002/D-007).
- **Error ledger:** 9 defect tổng — **8 closed, 1 open (E-008 S3, deferred, CTO-accepted D-007)**.
  Phân loại: security=5, contract=2, test=1, design=1. `errors.sh open` → **no open S1/S2**.
- **Review:** base round 1 (0 crit/5 HIGH/12 MED/10 LOW) → round 2 APPROVE; CHG-001 round 1
  (0 crit/1 HIGH R-C-001/1 MED/2 LOW) → round 2 APPROVE; CHG-003 round 1 APPROVE (1 MED/1 LOW).
- **santa-method cab-readiness:** D-002 + D-007, mỗi lần 2 checker PASS.
- **Tests tại go-live CHG-003:** make ci 2344/0 (sau re-embed NFR-003: product code 2376/0), cov ~90%,
  verify_tool_surface 50/50 (62 tool = contract, 0 write tool × 11 server), 4 test §6e giữ, permission
  regression trên pgvector thật 8/8 (0 restricted leak), grounding smoke 3/3.
- **Rollbacks:** 0. **Flaky/harness:** TC-069 (base) là lỗi harness, không phải defect sản phẩm.

## Defects
Từ `errors.sh list --feature mcp-data-platform` (root-cause mọi S1/S2 + mọi recurrence; mỗi dòng ngắn):

- **E-mcp-data-platform-001** · S2 security — introduced **backend**, escaped **backend → qa-plan →
  qa-verify**. GitLab `get_text()`/`get_job_trace()` buffer trace không giới hạn vào RAM (OOM-able);
  `response_too_large` khai báo nhưng không raise/test. **Root cause:** accessor `.text` viết cho body
  nhỏ ở Phase 1 bị tái dùng cho job-trace mà không xem lại đường download; model-output budget bị nhầm
  với download bound; không test body quá cỡ. Fix: stream + cap `Content-Length`/cumulative-byte raise
  `RESPONSE_TOO_LARGE` + 3 regression test. ✅ closed.
- **E-mcp-data-platform-002** · S2 security — introduced **backend**, escaped **backend → sa → qa-plan
  → qa-verify**. `DEFAULT_PATH_DENY` chỉ match tên kết thúc bằng suffix nhạy cảm → `.env.local`,
  `id_ed25519`, `*.key`, `*.p12`, `.npmrc`, `*.tfstate`… đều ALLOWED; `mcp_ingest` share list nên secret
  lọt vào `kb.chunks`. **Root cause:** deny-list minh họa bị coi là đầy đủ; các stage suy luận theo
  glob *có*, không theo tên secret *thiếu*; không test tên secret cụ thể bị chặn. Fix: mở rộng deny-glob
  match full-path + basename + test kế thừa cả hai phía. ✅ closed.
- **E-mcp-data-platform-003** · S2 security — introduced **backend**, escaped **backend → qa-plan →
  qa-verify**. Đường lỗi/log bỏ qua `scrub()` (`to_error_envelope` + `JSONStderrFormatter` in `str(exc)`
  thô), trái guarantee "scrub mọi thứ rời process", reachable từ 6 package. **Root cause:** scrub thêm ở
  success boundary + pipeline ingest, nhưng đường error-envelope + log-formatter dựng riêng, không đi
  qua cùng một choke point cấu trúc; guarantee ở docstring, không ở code; không test feed secret xuống
  đường lỗi/log. Fix: cả hai đường route qua `scrub()`/`_scrub_recursive` tại một điểm dựng + test
  end-to-end. ✅ closed. **(Lớp L-001; bị lặp lại ở E-007 và E-009.)**
- **E-mcp-data-platform-004** · S1 design — introduced **sa (ADR-0011 A3 wording) + qa (signoff/report
  wording)**, escaped **sa → qa-plan → qa-verify → release**. "recall ≥ 0.95" trình bày như bằng chứng
  NFR-003 nhưng chỉ đo ANN-index correctness (corpus+query cùng một seed dưới fake provider → ~1.0 kể cả
  embedding nhiễu). **Root cause:** hai nghĩa "recall" (index-correctness vs semantic-relevance) không
  tách; phép đo NFR-003 thật (bị chặn egress) không được flag UNVERIFIED cạnh con số. Fix: relabel
  ADR-0011 A3 + architecture.md NFR-003 cell + signoff; NFR-003 carried là Gate-C caveat + CEO-accepted
  residual. ✅ closed → **L-002**.
- **E-mcp-data-platform-005** · S3 contract — introduced **E1/E2 (contract widen không regen artifact
  dẫn xuất)**, escaped **ba/sa → be-E1 → be-E2 → qa-plan**. 3 test đỏ: pgvector snapshot thiếu enum
  `jira`; `verify_tool_surface` hard-code 49 ≠ 62. **Root cause:** contract (SSOT) mở rộng nhưng
  snapshot + script đếm (dẫn xuất, không auto-gen bởi make ci) không regen → drift âm thầm tới khi test
  so-sánh dẫn-xuất-vs-contract chạy. Fix: E4 regen snapshot + bump count; regression test pin dẫn-xuất
  vào contract. ✅ closed. (S3; không bắt buộc root-cause nhưng ghi để trọn pattern.)
- **E-mcp-data-platform-006** · S2 contract — introduced **E5 (reconcile, chạy song song)**, escaped
  **E5 (chưa verify)**. `get_jira_context` reconcile phát claim `LOW_CONFIDENCE` có
  `provenance[0].confidence = None`, vi phạm schema (phải là number). **Root cause:** transient trong
  batch song song E5 — wiring confidence/freshness đang dở; red-window trong file dùng chung giữa E5 và
  E6. Fix: E5 hoàn tất gán confidence; test contract-validate `provenance[].confidence` là number. ✅
  closed. **Prevention của class song-song:** cross-batch full-suite run (E6) lộ ra red-window, chứng
  minh guard chéo hoạt động.
- **E-mcp-data-platform-007** · S2 security — introduced **backend (E6, T-104) + sa (ADR-0018 §7 /
  ADR-0021 khẳng định một choke point tier-wide)**, escaped **sa → backend (E4/E6) → qa-plan → qa-verify**.
  Permission choke point #1 chỉ gắn trên 2/8 content tool; 6 tool kia đọc `kb.*` + trả provenance KHÔNG
  qua filter = default-allow bypass quanh choke point "duy nhất"; v1 không rò chỉ nhờ bất biến corpus
  team-only (ADR-0016), không nhờ choke point. **Root cause: LẶP LẠI L-001 / E-001/002/003** — guarantee
  khẳng định tier-wide trong prose nhưng enforce 1/4 bề mặt; test (TC-090/091) chỉ chạy 2 tool có
  grounding, không feed input adversarial cho 6 tool kia. Fix: một quyết định `enforce_permission`
  default-deny tier-wide cho cả 8 tool (một `document_grants` read), đóng cả bypass snapshot của
  `get_jira_context`; test live trên pgvector thật 8/8 (0 restricted leak) + test enumerate cả 8 tool.
  ✅ closed → củng cố **L-001**, seed **L-004** (enumerate mọi đường).
- **E-mcp-data-platform-008** · S3 test — introduced **qa-plan (base test plan)**, escaped **qa-plan
  (base + CHG-001)**. Must FR-005 (Kibana) chỉ có TC-019/020 integration-level, thiếu E2E TC. Impact
  LOW (Kibana phủ bởi integration + 9-server stdio E2E TC-070). **Trạng thái: OPEN, deferred,
  CTO-accepted (D-007)** — sửa cần renumber base TC (protocol §4 cấm giữa change). Carried-forward.
- **E-mcp-data-platform-009** · S2 security — introduced **CE5/CE2 (CHG-003)**, escaped **ce5/ce2 →
  qa-plan → be**. `register_secret()` (scrub token theo giá trị) được xây + chứng minh TC-115 nhưng
  **không nơi nào gọi ở production** (grep chỉ thấy ở `redact.py` + TC-115) → token mờ (opaque) sẽ
  không bị scrub trên đường lỗi/log thật. **Root cause: LẶP LẠI L-001 / E-003 / E-007** — guarantee đạt
  trong test (test tự đăng ký token) nhưng không enforce tại seam production; DoD "test xanh" không phân
  biệt "cơ chế xanh" với "được nối vào đường thật". Fix: gọi `register_secret`/`register_dsn_secret` tại
  11 constructor client + test wiring per-source (dựng client với secret mờ, không gọi register tay,
  rồi assert scrub ở cả result + stderr). ✅ closed → seed **L-004**.

**RECURRING (`errors.sh summary`):** lớp L-001 (guarantee ở prose, không enforce tại một choke point
mọi đường đi qua, thiếu adversarial test) đã lặp **ba lần** trong feature: E-001/002/003 (base) →
E-007 (permission 2/8 tool) → E-009 (mechanism built-but-unwired). Đây là tín hiệu đủ mạnh để thêm
L-004 (mechanism built ≠ wired; enumerate mọi đường + test wiring production) và nhấn mạnh L-001.

## What worked
- **Bất biến giữ nguyên dù mở egress thật.** 9 server vẫn read-only + stdio, 0 port mới; egress
  default-deny một choke point `check_egress` (atlassian ALLOWED; huggingface.co + evil.example.com
  DENIED khi ngoài allowlist); token không rò (`leak_flag=0`). Mở biên giới ngoài nhưng không nới một
  bất biến an toàn nào ngoài đúng hai thứ CEO duyệt.
- **Tách bạch "đo được" vs "đã chứng minh" (L-002 áp đúng).** Lúc đo NFR-003: tách (A) ANN-correctness
  recall 1.0 nhưng `hnsw_index_used=false` (27 chunk nhỏ → seq-scan) KHÔNG phải NFR-003, và (B) NFR-003
  semantic hit@5 8/8 cỡ mẫu nhỏ. Không để một con số đọc thành bằng chứng.
- **Choke-point + adversarial test là tuyến phòng thủ thật.** Chính kỷ luật L-001 làm review CHG-001
  bắt được R-C-001 (permission 2/8 tool) và E-009 (register_secret unwired) trước khi lên live.
- **Deploy dừng-và-báo khi gặp lỗi.** `doctor` từ chối token write-capable → deploy dừng Step 1 báo
  CEO thay vì tự lách; rồi dùng escape-hatch có CTO phê duyệt (D-008) tường minh, bề mặt vẫn 0 write tool.
- **Review bắt mọi HIGH trước live env** (cả ba vòng review): 0 defect escaped ra prod.

## What hurt
- **Mechanism built nhưng không wired ở production (E-009).** 5-whys: token-không-rò đạt trong test →
  vì test tự đăng ký token → vì thiếu test chứng minh **wiring tại seam production** → vì DoD "test
  xanh" không tách "cơ chế xanh" khỏi "được gọi trên đường thật". Fix: đăng ký tại 11 constructor + test
  wiring per-source → **L-004**. Blameless: khoảng trống giữa "cơ chế có" và "cơ chế được nối".
- **Permission choke point gắn thiếu đường (E-007).** 5-whys: như E-003 — guarantee tier-wide trong
  prose, test chỉ chạy 2/8 tool, không feed input adversarial cho 6 tool kia. Lần lặp L-001 thứ hai →
  củng cố L-001 + góc "enumerate MỌI đường" (L-004).
- **Egress gate chỉ bọc đường được duyệt; một đường off-by-default không bọc (R-C3-001, MEDIUM, mở).**
  `HttpEmbeddingProvider` dựng httpx thô, không qua egress guard — pre-existing, tắt-mặc-định, không
  sai AC/NFR. Carried làm residual + **DK4** (harden trước khi dùng provider=http) → **L-005**.
- **CHG-001 build đầy đủ nhưng chưa go-live** — một deliverable lớn (24 task, APPROVE) "đóng băng chờ"
  vì CEO đổi ưu tiên sang làm thật. Là lựa chọn của CEO, không phải lỗi, nhưng là WIP tồn kho cần theo
  dõi (DK2 migration-locking đã làm ở E1; artifact/ADR giữ nguyên).
- **Kỷ luật layout evidence trôi** — `records/backlog.md` ngoài layout + một số file evidence/probe
  (`qa-dev` probe + driver `deploy-prod` + `__pycache__`) không đúng tên `<YYYYMMDD-HHMMSS>-<kebab>.<ext>`
  → `layout.sh check` FAIL. Release role đã ghi; không chặn go-live (evidence đọc được) nhưng cần dọn.

## Residual / mang theo khi đóng feature
1. **E-008** (S3, test, OPEN/deferred) — thiếu E2E TC cho Must FR-005 (Kibana); CTO-accepted (D-007);
   sửa ở change kế tiếp cho phép renumber base TC.
2. **R-C3-001** (MEDIUM) — `HttpEmbeddingProvider` tắt-mặc-định, chưa bọc egress guard → **DK4: harden
   trước khi dùng provider=http**.
3. **NFR-003 full verification** — mới "đo lần đầu (cỡ mẫu nhỏ)". Cần corpus EA lớn hơn + golden-set đã
   kiểm chứng + hiệu chỉnh ngưỡng τ; chưa phải "verified trên dữ liệu công ty".
4. **CHG-001 Company Knowledge** — vẫn **POSTPONED** (artifact/ADR giữ; DK2 đã xong; DK3 Redis ACL không
   tái dùng cho shared còn hiệu lực).
5. **Hai lỗi layout-check pre-existing** (release role ghi): `records/backlog.md` ngoài layout + một số
   file evidence/probe qa-dev + driver deploy-prod không đúng tên — cần dọn, không chặn.

## Lessons
Append vào `docs/squad/knowledge/lessons.md` (không trùng L-001/L-002; E-007 củng cố L-001, không tạo
lesson riêng). Hai lesson mới:
- **L-004** · all — *mechanism built ≠ mechanism wired*: một guarantee cần cơ chế **và** test dựng
  đường production thật chứng minh nó được gọi (không chỉ test cơ chế tự-đăng-ký); enumerate MỌI đường
  guarantee phải phủ. [E-009 lặp E-003/E-007; E-007 lặp L-001]
- **L-005** · squad-cto — khi mở biên giới mới (egress/vendor/credential) phải nối ngay vào MỘT choke
  point default-deny có adversarial test **cả chiều cho-phép lẫn chiều từ-chối**, và liệt kê rõ đường
  nào CHƯA qua guard (off-by-default cũng ghi làm residual, không im lặng). [E-009, R-C3-001, ADR-0023 §6e]

## Distill
- **KHÔNG due** (`knowledge.sh due` = 0/3 finished feature kể từ distill trước, 3/40 active lesson; nay
  5 sau L-004/L-005). Không distill lượt này; để distill gộp/promote sau.
