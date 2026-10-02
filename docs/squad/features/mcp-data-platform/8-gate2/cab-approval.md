# CAB approval — mcp-data-platform · CHG-003

- Change ID: CHG-mcp-data-platform-chg003-20261002
- Status: approved
- Approved by: CEO
- Approved at: 2026-10-02T14:57:00+07:00
- Gate: Gate 2 (CAB go-live)
- Decision (CEO, nguyên văn ý định): "duyệt go-live, bật ingest thật với confluence" — Option 1.
- Scope approved: CHG-003 — mở egress thật cho đường ingest-pull (`*.atlassian.net`, Confluence Cloud
  `https://tnexwm.atlassian.net` trước) + tải model embedding thật một lần từ `huggingface.co` (khi cần
  đo NFR-003); Atlassian là vendor; token read-only; runbook CLI 9 source (4 ingestable + 5 live-only).
- DK1 accepted: CEO chấp nhận ship v1 với NFR-003 (chất lượng semantic) CHƯA đo; đo thật bằng model
  thật là bước @live/spike S2 sau go-live.
- Target: máy local của CEO (per-user stdio; prod=local). Go-live tag `release/mcp-data-platform-chg003-20261002`.
- Source: 7-release/cab-pack.md; CTO READY_FOR_CAB = D-007.
- CHG-001 Company Knowledge: POSTPONED — không nằm trong go-live này.

## Điều kiện go-live (CTO D-007)
- DK2: smoke prod + cửa sổ quan sát 30 phút xanh (liveness + egress-default-deny + token-không-rò);
  bất kỳ rollback trigger → rollback ngay <60s + Gate 2 mới.
- DK4: hardening R-C3-001 trước khi dùng provider=http; fix E-008 ở change kế tiếp an toàn renumber.
- DK5: ghi id snapshot Postgres khôi phục được trước lần pull thật đầu tiên.

## Credential (CEO cung cấp)
- Token read-only Atlassian: `/Users/manh.le/Desktop/MCP/.token-key` (git-ignored).
- File khai báo thật: `credentials/.ingest-sources.env` (file ẩn, git-ignored) — trỏ
  `MCP_CONFLUENCE_API_TOKEN_FILE` tới token trên. Còn CHỜ CEO điền: `MCP_CONFLUENCE_EMAIL` +
  `MCP_INGEST_CONFLUENCE_TEAM_SPACES`.
- Template chuẩn (committable): `credentials/.ingest-sources.env.example`.
