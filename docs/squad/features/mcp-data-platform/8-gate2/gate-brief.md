# Gate 2 brief — mcp-data-platform · CHG-003 (real ingestion + egress, Confluence first)

> Gate 2 cho **baseline thứ ba** (CHG-003). Baseline 9-source đã live local (1/10). CHG-001 Company Knowledge
> **đang hoãn** (không nằm trong lần go-live này). CHG-003 build xong, QA PASS, review round 1 **APPROVE**,
> CTO D-007 **READY_FOR_CAB = YES**. Nguồn: 7-release/cab-pack.md.

## Summary (shown in chat, ≤ 10 lines)
- Thay đổi: **mở egress thật** cho đường ingest-pull (`*.atlassian.net`, Confluence Cloud `tnexwm.atlassian.net` **trước**) + tải model embedding thật một lần từ `huggingface.co`; thêm **Atlassian là vendor** + lưu **token read-only**; **runbook CLI cho cả 9 source** (4 ingest được + 5 live-only).
- Bất biến vẫn giữ: 9 server read-only + stdio (NFR-005), **0 tool ghi** (62 tool), egress **default-deny 1 choke point**, token **scrub cả 2 chiều**; choke point permission/grounding của CHG-001 không đổi.
- Chất lượng: review round 1 **APPROVE** (0 critical / 0 high / 1 medium / 1 low). Defect: 9 tìm / 8 đóng / **1 chấp nhận-mở** (E-008 S3, lỗ hổng cũ Kibana thiếu test E2E) / **0 open S1/S2**.
- Bằng chứng: make ci xanh **2344 passed / 0 failed** (~90% cov), verify_tool_surface **50/50** (62=contract, 0 write × 11 server), **4 test đối kháng §6e đều đạt** (egress default-deny không mở socket, allow-list fail-closed, token không rò + wiring thật E-009, server read-only+stdio), Confluence Cloud e2e trên pgvector thật, chặn-rò restricted trên nội dung ingest thật.
- Rủi ro: **medium**. Rollback **<60s** (bỏ `MCP_EGRESS_ALLOWLIST` + gỡ token + dừng ingest connector); không có schema mới; egress thật mặc định TẮT (gated sau `MCP_INGEST_ALLOW_LIVE_EGRESS`).
- **Cần CEO chấp nhận rủi ro (blocking, giống DK1 lần trước):** NFR-003 chất lượng semantic **vẫn chưa đo** — egress giờ có thể mở nhưng chưa chạy golden-set thật; cả CI lẫn dev đều dùng fake provider. Đo thật = bước @live (tải bge-m3 ~2GB) + spike S2.
- Caveat khác (không block): τ chưa calibrate; R-C3-001 (HttpEmbeddingProvider off-by-default chưa qua egress-guard, cần hardening trước khi dùng provider=http); E-008 S3; nối tenant thật + đăng ký Claude Desktop (NFR-005) là bước tay cần token read-only + mạng.
- DEPLOY_MODE=script → nếu duyệt, bước deploy local + lần egress thật ĐẦU TIÊN cần anh xác nhận + cung cấp **token Atlassian read-only** lúc chạy.
Full brief: docs/squad/features/mcp-data-platform/7-release/cab-pack.md · sources: cab-pack.md

## Details

### Change record
- Baseline CHG-003 trên base b898040 (branch `claude/zealous-johnson-yb3t2q`). Go-live tag đề xuất `release/mcp-data-platform-chg003-20261002`.
- Target: máy local của CEO (per-user stdio; prod=local). Không có service UAT/PRE dùng chung → PRE-equivalent = dev host thật + Docker pgvector 0.8.6.
- CHG-001 Company Knowledge **hoãn**, giữ nguyên artifact (Appendix trong cab-pack); không nằm trong go-live này.

### Phạm vi CHG-003
- Egress default-deny, allow-list chỉ host nguồn cho đường ingest-pull (`*.atlassian.net` trước; gitlab/opensearch/jira khi cấu hình) + `huggingface.co` cho 1 lần tải model.
- Atlassian = vendor; token API **read-only least-privilege** qua env/*_FILE, không commit/log/trả về (scrub 2 chiều); `doctor` từ chối tài khoản có quyền ghi.
- Runbook CLI 9 source, Confluence trước: 4 **ingest được** (Confluence, GitLab, OpenSearch, Jira: doctor→ingest→status→verify kb_semantic_search) + 5 **live-only** (CloudWatch, Kibana, Kafka, Redis, SQS/SNS: doctor→đăng ký→tools/list; KHÔNG ingest).

### Defects (errors.sh)
- E-001..E-007 đóng (base + CHG-001). E-009 (S2, token-scrub chưa wire ở production) **đã fix + QA verify-close**. E-008 (S3, Kibana FR-005 thiếu test E2E, lỗi cũ) **chấp nhận-mở**, hoãn cho CTO. 0 open S1/S2.

### CTO conditions (D-007 READY_FOR_CAB)
- DK1 (blocking Gate 2): CEO chấp nhận ship với NFR-003 chưa đo, nếu không thì hoãn tới khi đo bge-b3/S2.
- DK2 (sau cut-over): smoke prod + cửa sổ quan sát 30 phút xanh (liveness + bất biến egress-default-deny + token-không-rò); bất kỳ trigger → rollback ngay <60s + Gate 2 mới.
- DK3 (deploy guard, đã xác nhận): cần `cab-approval.md` CHG-003 status approved mới; approval 1/10 KHÔNG phủ change này.
- DK4: hardening R-C3-001 trước khi dùng provider=http; fix E-008 ở change kế tiếp an toàn renumber.
- DK5: ghi lại id một snapshot Postgres khôi phục được trước lần pull thật đầu tiên.

### §11 Operator runbook (anh sẽ chạy để bật thật — xem cab-pack §11 / architecture Appendix A)
1. `export MCP_CONFLUENCE_BASE_URL=https://tnexwm.atlassian.net/wiki`
2. `export MCP_CONFLUENCE_FLAVOR=cloud`
3. `export MCP_CONFLUENCE_EMAIL=<email service account>`
4. `export MCP_CONFLUENCE_API_TOKEN_FILE=/path/confluence.token`  (token **read-only**, không commit)
5. `export MCP_EGRESS_ALLOWLIST='*.atlassian.net'`  (bắt buộc — nếu không, pull bị default-deny)
6. `export MCP_INGEST_ALLOW_LIVE_EGRESS=true`  (chỉ cho lần chạy thật)
7. `mcp-confluence doctor` → `mcp-ingest run --source confluence` → `mcp-ingest status --json` → verify `kb_semantic_search`
- Tạo token read-only: Atlassian account → Security → API tokens (mô tả trong cab-pack; **không nhúng token vào repo**).

### Rollback
- <60s: `unset MCP_EGRESS_ALLOWLIST` + gỡ token + dừng ingest connector. Không schema mới. Egress thật default-off.

### Lựa chọn cho CEO tại Gate 2
1. **Duyệt go-live** (chấp nhận NFR-003 chưa đo cho v1; bật ingest thật Confluence trước qua CLI).
2. **Hoãn** (đặt cửa sổ mới) — ví dụ muốn đo NFR-003 bằng model thật trước khi go-live.
3. **Từ chối**.
