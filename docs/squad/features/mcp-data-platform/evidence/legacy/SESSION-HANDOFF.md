# Session handoff — mcp-data-platform (2026-10-01, cloud → local)

**Đọc `state.json` trước** — đó là nguồn sự thật về stage/gate/quyết định.
Branch: `claude/zealous-johnson-yb3t2q` (remote `origin`). Chưa merge vào `main`, chưa có PR.

## Đang ở đâu
- Stage hiện tại: **qa-verify, run 1 — dừng giữa chừng** (user hết credit cloud).
- Gate A, B: approved. Gate C: pending.
- Backend: xong 86/86 task (T-067 đóng; T-086 chỉ có quy trình). Frontend: skipped (không có task).
- `make ci` xanh: 1975 passed, 11 skipped (`@live`), coverage 95.9%.

## Việc tiếp theo (theo thứ tự)
1. `git pull origin claude/zealous-johnson-yb3t2q`, `uv sync --all-packages`.
2. `/squad continue mcp-data-platform` → re-dispatch `squad-qa` mode verify run 1:
   - `e2e/` đã có 4 file test + harness (WIP, commit cùng handoff này).
   - Lỗi đã thấy: `e2e/test_stdio_readonly_e2e.py::test_TC_069_stdout_carries_only_jsonrpc_frames[confluence]`
     → `ValueError: I/O operation on closed file` trong `subprocess.communicate` (test đóng stdin rồi gọi communicate).
     Có vẻ là lỗi harness test, chưa xác nhận. 31 test e2e trước đó pass; phần còn lại chưa chạy.
   - Ghi `regression-report.md`.
   - Trên máy local có Docker: có thể chạy thêm `MCP_LIVE_TESTS=1` cho compose (Postgres+pgvector ≥0.8, Redis, Kafka, LocalStack).
3. `review` (squad-reviewer) → Gate C.

## Ghi chú cho review (đã biết)
- GitLab job trace tải về không giới hạn dung lượng.
- Deny-glob `*secret*` rộng (chặn cả `docs/secrets-management.md`).
- Runbook ở `infra/scheduler/runbook-ingest.md` (plan ghi `docs/runbook-ingest.md`).
- `prune` bắt buộc `--older-than` (chặt hơn contract `anyOf`).
- `status --json` không có số `ingest_failures` (contract không có trường).
- `ruff format --check` báo một số file chưa format (không nằm trong `make ci`).

## Việc cần người/môi trường thật (checklist Gate C)
- S1: chạy `scripts/probe_reachability.py` trên máy có VPN → điền `docs/spikes/S1-reachability.md`.
- S2: HF bị chặn trên cloud → chạy bake-off (`docs/spikes/S2-embedding-bakeoff.md` bước 3); model tạm = bge-m3, ADR-0010 còn proposed.
- S3 (T-086): cần corpus thật.
- Sign-off tay: `docs/signoff/phase-1.md`, `phase-2.md`, `phase-3.md` (doctor live, đăng ký Claude Desktop, chạy eval thật).
- Xác nhận shape API thật Confluence/GitLab (restrictions, permissions) — nhãn `visibility` phụ thuộc vào đó.

## Câu hỏi còn chờ user/PO
- Chấp nhận rủi ro ADR-0016 A3 (trang bị thêm restriction mà nội dung không đổi vẫn tìm được tới lần full reconcile, đề xuất 24h)?
- Danh sách `MCP_INGEST_CONFLUENCE_TEAM_SPACES`, `MCP_INGEST_GITLAB_TEAM_PROJECTS`.
- Ngưỡng NFR-002/003/004, retention mặc định của `prune`, cadence scheduler (đang `# THRESHOLD TBD`).
