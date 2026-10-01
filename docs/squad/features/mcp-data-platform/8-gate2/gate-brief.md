# Gate 2 brief — mcp-data-platform (CAB go-live)

## Summary (shown in chat, ≤ 10 lines)
- Thay đổi: 9 MCP server read-only (Confluence, GitLab, OpenSearch, Kibana, CloudWatch, Kafka, Redis, SQS/SNS, Postgres+pgvector) chạy local qua stdio + pipeline ingest/embedding.
- Chất lượng: review round 2 **APPROVE**, 0 critical / 0 high. Defect: 4 tìm thấy / 4 đóng / 0 chấp nhận-mở.
- Rủi ro: **medium**. Rollback **<60s** (gỡ stdio entry + dừng scheduler); migration expand-only.
- CTO: **READY_FOR_CAB** (D-002), santa-method 2 checker đều PASS.
- **Cần CEO chấp nhận rủi ro (ĐK1, blocking):** NFR-003 (chất lượng semantic) CHƯA đo được — số recall 0.95 chỉ là tính đúng của ANN-index dưới fake provider; đo thật bị chặn bởi HF egress + ADR-0010 chưa chốt model.
- Caveat khác (không block): 153 pg-test skip trên non-Debian (đã phủ qua e2e); CI chạy tay; migration an toàn cho lần deploy đầu; live-source check cần VPN/creds (sign-off tay).
- CTO khuyến nghị: **chấp nhận cho v1** (read-only, không mất dữ liệu, đo thật bị chặn môi trường), đo NFR-003 trong CHG-001 khi egress/ADR-0010 được giải quyết.
- DEPLOY_MODE=script → nếu duyệt, production deploy sẽ cần bạn xác nhận + Kiro permission prompt.
Full brief: docs/squad/features/mcp-data-platform/7-release/cab-pack.md · sources: cab-pack.md

## Details

### Change record
- Feature: mcp-data-platform (tier large). Branch `claude/zealous-johnson-yb3t2q` @ 0363fcc. Chưa merge main, chưa PR.
- Go-live tag đề xuất: `release/mcp-data-platform-20261001`.
- Kiến trúc: per-user local stdio trong Claude Desktop/Code; không có service UAT/PRE dùng chung → PRE-equivalent verification chạy trên dev host thật + Docker (e2e 34/34, 10/10 P1 pgvector trên PostgreSQL 16.15 + pgvector 0.8.6, coverage 89.97%).

### Defects (E-001..E-004, tất cả đóng + verified)
- E-001 / R-001: GitLab job-trace nay bounded (RESPONSE_TOO_LARGE, cap 10 MiB).
- E-002 / R-002: deny-glob mở rộng (.env.*, id_ed25519, *.key/*.p12/*.pfx/*.jks, *.tfstate*, .npmrc/.netrc).
- E-003 / R-003: error envelope + log stderr đều qua scrub().
- E-004 / R-004: nhãn recall sửa lại = ANN-index correctness; NFR-003 đánh dấu UNVERIFIED (signoff + ADR-0011 A3 + architecture.md).

### Gate-C caveats (CTO xác nhận, đưa lên CEO)
1. **NFR-003 semantic quality UNVERIFIED** — ĐK1 blocking: cần CEO/PO chấp nhận ship v1 chưa đo, nếu không thì hoãn go-live.
2. 153 package-level pg test skip trên non-Debian (hành vi đã phủ qua e2e MCP_E2E_PG_URL). Owner backend.
3. CI chưa có runner enforce; 5 gate chạy qua `make ci` thủ công.
4. Migration-locking an toàn cho lần deploy đầu (bảng rỗng); xem lại trước lần đổi schema sau (trước CHG-001 epic E1).
5. Live-source check (Confluence/GitLab shape, doctor live, đăng ký Claude Desktop, eval thật) = checklist sign-off tay cần VPN/creds.

### Deferred (residual risk v1): R-006..R-027 (22 medium/low) — xem cab-pack.md §9.

### CTO conditions (D-002)
- ĐK1 (Gate 2 blocking): CEO/PO chấp nhận NFR-003 chưa đo, hoặc hoãn.
- ĐK2 (sau go-live, trước CHG-001 E1): fix R-006/R-007 migration-locking.
- ĐK3: Redis dev ACL (R-013) không tái dùng cho staging/shared.
- ĐK4: follow-up R-005-secondary / R-012 / R-016.
- Smoke + cửa sổ quan sát 30 phút phải xanh sau cut-over; bất kỳ rollback trigger → rollback ngay + Gate 2 mới.

### Rollback
- <60s: gỡ per-user stdio entry khỏi Claude Desktop/Code config + dừng scheduler ingest. Migration expand-only, không phá dữ liệu.
