# Sign-off Phase 3 — SQS/SNS, pgvector, ingest pipeline + kiểm chứng toàn nền tảng (T-085)

Trạng thái: **phần tự động xong và xanh (`make ci` exit 0); phần thủ công cần nguồn thật / model
embedding thật / Docker / Claude Desktop CHƯA làm được trong container của squad** (không có VPN,
không có Confluence/GitLab/OpenSearch/AWS, Docker daemon không chạy, không tải được trọng số model,
không có Claude Desktop). Các mục thủ công để trống có chủ đích, không được đánh dấu giả. Test
`@pytest.mark.live` đã viết nhưng **chưa chạy**. Mọi ngưỡng còn chờ PO mang comment
`# THRESHOLD TBD (...)` để QA grep ra được.

## A. Đã kiểm chứng tự động (`make ci` xanh)

Kết quả gần nhất: **1975 test pass, 11 skip** (`@pytest.mark.live`: cần credential/VPN/Docker);
suite read-only 167 test; ruff, mypy, `validate-contract` xanh. Coverage: `mcp_ingest` 95.7%,
`mcp_pgvector` 96.6%, `mcp_common` 95.8%, `mcp_redis` 94.9%, `mcp_gitlab` 95.1% (mọi package ≥ 80%;
tổng 95.9%).

| Mục | Bằng chứng |
|---|---|
| **49 tool khớp contract**: 9 server dựng được không cần credential, tổng 49 tool (OpenSearch bật DSL) và bằng đúng tập operation non-CLI của `api-contract.yaml`; snapshot từng package khớp contract | `scripts/verify_tool_surface.py`, `packages/mcp_ingest/tests/test_signoff_surface.py` |
| **0 tool ghi trên cả 9 nguồn**; `assert_readonly_tool_surface` chạy trên 9 package, **gồm cả method mà `mcp-ingest` gọi** (connector: Confluence 2 op, GitLab 7 op, OpenSearch 1 op đều nằm trong allowlist của `client.py`) | `verify_tool_surface.py` (`additional_client_operations`), `test_connector_isolation.py` |
| Tool ghi không tồn tại bị SDK từ chối ở tầng JSON-RPC cho **cả 9 server** (5 tên × 9) | `verify_tool_surface.py`; `test_FR_014_AC_002_*` của từng package |
| **3 prompt** hiện diện (`dev_knowledge_lookup`, `incident_investigation`, `semantic_synthesis`) và **6 lệnh CLI** (`db upgrade`, `run`, `status`, `sources`, `reembed`, `prune`) đều có `--help` | `verify_tool_surface.py`; `test_cli_contract.py` |
| Mỗi server có `doctor`/`serve`/`tools-dump` (lệnh `doctor` chạy thật cần nguồn thật: xem B) | `verify_tool_surface.py` |
| **`mcp_ingest_rw` không xuất hiện trong env của bất kỳ MCP server nào**: không trong snippet `config-emit` của 9 server, không trong mã nguồn server nào, không trong ví dụ cấu hình Claude Desktop ở `docs/`; kiểm âm có test | `verify_tool_surface.py`, `test_signoff_surface.py` (3 test âm) |
| `MCP_INGEST_ADMIN_DSN` chỉ được `db upgrade` đọc; mọi lệnh khác dùng `MCP_INGEST_PGVECTOR_DSN`; thiếu cả hai → `db upgrade` báo lỗi config rõ ràng; DSN không lọt vào thông báo lỗi kết nối | `test_cli_contract.py` (`test_no_other_command_reads_the_admin_dsn`, `test_the_admin_dsn_is_referenced_only_by_migration_dsn`) |
| `mcp-pgvector` từ chối serve khi được dán DSN `mcp_ingest_rw` (lỗ hổng ADR-0003 A1) | `mcp_pgvector/tests/test_db_integration.py::test_TC_043_*` |
| **Recall NFR-003 ≥ 0.95**: 50 truy vấn, top-10, đường tìm kiếm thật của `mcp-pgvector` so với brute force (`enable_indexscan=off`), HNSW được ép dùng (kiểm bằng `EXPLAIN`), corpus ~1320 chunk nạp **qua pipeline**. Kết quả: **mean 0.994, min 0.90** trên pgvector 0.6.0 (nhánh over-fetch `top_k×4`) | `packages/mcp_pgvector/tests/test_recall.py`, `scripts/recall_benchmark.py`, `eval/recall_queries.yaml` |
| `mcp_query_ro` không ghi được (INSERT/UPDATE/DELETE/TRUNCATE/DROP) | `test_recall.py::test_NFR_001_*`, `test_db_integration.py::test_TC_044_*` |
| Pipeline FR-012: AC-001 (crawl→redact→chunk→embed→persist, tìm lại qua `kb_semantic_search` với **URL gốc**), AC-002 (nguồn chết không dừng nguồn khác, cursor không nhảy, doc lỗi kéo cursor lùi, lock theo nguồn), AC-003 (chạy 2 lần không nhân bản, đổi tên giữ hash vẫn cập nhật citation, đổi chunk config thì re-chunk) | `test_integration.py`, `test_checkpoint.py`, `test_persist_idempotent.py` |
| R4: secret trong nguồn **không** vào `kb.chunks` (truy vấn thật); deny-glob chặn `.env`/`*.pem` và để lại hàng `ingest_failures{redact, blocked_by_policy}` | `test_stage_redact.py`, `test_connector_gitlab.py` |
| ADR-0016 (team-only): `visibility != 'team'` bị từ chối; `SELECT count(*) FROM kb.documents WHERE visibility <> 'team' AND deleted_at IS NULL` = 0; **relabel team→restricted xoá chunk + tombstone trong một transaction** + ghi `ingest_failures` | `test_stage_redact.py::test_ADR_0016_*` |
| Safety valve reconcile: crawl chết ở 30% không tombstone; crawl đủ thì tombstone + xoá vật lý chunk | `test_reconcile_valve.py` |
| `reembed` resume được, không đụng `documents`, `mcp-pgvector` chấp nhận kho sau re-embed (và từ chối trước đó) | `test_reembed.py` |
| `prune` bắt buộc `--older-than`, mặc định dry-run, chỉ xoá đúng bia mộ đủ tuổi | `test_prune.py`, `test_cli_contract.py` |
| Scheduler: mẫu cron/launchd hợp lệ; **job chạy thật một lần** qua `infra/scheduler/run-ingest.sh` (CLI thật, Postgres cục bộ, hai HTTP stub cho Confluence và embedding OpenAI-compatible) và ghi `ingest_runs` | `test_scheduler.py` |
| Redis `+select` (SA reconcile #6): `db=1` hoạt động, `SELECT`/`SWAPDB`/`MOVE` không bao giờ là lệnh của tool, startup check không coi `+select` là quyền ghi | `mcp_redis/tests/test_local_redis.py`, `test_tools_readonly.py` |
| Eval Phase 3: 12 câu, có hai case phân biệt "không có dữ liệu index" với "bộ lọc loại hết", case ARN queue, case secret/HR | `mcp_pgvector/tests/test_eval_phase3.py` |

### Giới hạn của bằng chứng tự động (nêu thẳng)

* **Recall đo bằng provider giả** (`DeterministicFakeProvider`, hashed bag-of-words) trên corpus tổng
  hợp: nó chứng minh index HNSW + đường truy vấn, **không** chứng minh chất lượng ngữ nghĩa. Lần đo
  với model thật (spike S2 chưa chốt: `bge-m3` đang là tạm thời, ADR-0010 A1) **chưa làm** — xem B.
* pgvector cục bộ là **0.6.0** (< 0.8): nhánh `hnsw.iterative_scan` chưa được chạy với DB thật;
  nó nằm ở test `@live` của compose (`mcp_pgvector/tests/test_integration.py`).
* Connector Confluence/GitLab/OpenSearch được test bằng fixture viết tay theo tài liệu API công
  khai (respx/stub), **chưa** với tenant thật (spike S1: không tới được). Hình dạng của
  `expand=restrictions.read...` (Confluence) và `permissions.*.access_level` (GitLab) là điểm phải
  xác nhận trên hệ thật vì nhãn `visibility` phụ thuộc vào đó.
* `doctor` của 9 server cần nguồn thật; chỉ kiểm được sự hiện diện của lệnh.

## B. Checklist thủ công (người có nguồn thật + model thật điền)

- [ ] Chốt model embedding (spike S2, ADR-0010), đặt `MCP_INGEST_EMBEDDING_*`; chạy
      `MCP_INGEST_ADMIN_DSN=... uv run mcp-ingest db upgrade` trên Postgres thật. Kết quả: ______
- [ ] Tạo role: `ALTER ROLE mcp_ingest_rw PASSWORD ...`, `ALTER ROLE mcp_query_ro PASSWORD ...`
      (ngoài git). Kết quả: ______
- [ ] `uv run mcp-ingest sources` báo `configured=true` cho Confluence và GitLab; OpenSearch
      `enabled=false` (mặc định). Điền `MCP_INGEST_CONFLUENCE_TEAM_SPACES`,
      `MCP_INGEST_GITLAB_PROJECTS`, `MCP_INGEST_GITLAB_TEAM_PROJECTS`. Kết quả: ______
- [ ] **Xác nhận nhãn `visibility` trên hệ thật** (S5): trang Confluence có read-restriction và trang
      con của nó bị `restricted`; project GitLab private chưa khai báo bị `restricted`; issue
      confidential bị `restricted`. Chạy `uv run mcp-ingest run --source confluence --limit 20 --dry-run`
      rồi chạy thật và kiểm `SELECT visibility, count(*) FROM kb.documents GROUP BY 1` (kỳ vọng chỉ
      `team`) và `kb.ingest_failures`. Kết quả: ______
- [ ] Chạy full ingest đầu tiên: `uv run mcp-ingest run --source all --mode full`; exit code 0 hoặc
      giải thích được; `uv run mcp-ingest status` có `last_success_at`. Kết quả: ______
- [ ] Đo recall NFR-003 với model thật: `uv run python scripts/recall_benchmark.py --dsn <mcp_query_ro>
      --provider configured --queries eval/<bộ truy vấn thật>.yaml` ≥ 0.95. Nếu `pgvector` ≥ 0.8
      (image compose `pgvector/pgvector:pg16`) đo luôn nhánh iterative scan. Kết quả: ______
- [ ] Chạy live test với `infra/docker-compose.yml`: `MCP_LIVE_TESTS=1 uv run pytest -m live
      packages/mcp_pgvector packages/mcp_sqs_sns`; số message trên LocalStack không đổi trước/sau.
      Kết quả: ______
- [ ] Cài lịch (`infra/scheduler/`), chạy `run-ingest.sh incremental` tay một lần và một lần `full`;
      đọc theo `runbook-ingest.md` cả ba exit code. **Cadence chờ PO xác nhận** (Open question 5).
      Kết quả: ______
- [ ] `uv run mcp-<nguồn> doctor` báo `ok` cho cả **9** server (mục B của signoff Phase 1 và 2 cho
      7 server trước; thêm `mcp-sqs-sns doctor` và `mcp-pgvector doctor`). Kết quả: ______
- [ ] Dán JSON từ `uv run mcp-common config-emit --server <tên>` cho các server cần dùng vào
      `claude_desktop_config.json` (không bật cả 9 cùng lúc, README mục 2); khởi động lại Claude.
- [ ] Các server hiện trong Claude: tổng **49** tool (48 nếu không bật `MCP_OPENSEARCH_ALLOW_DSL`),
      3 prompt, 0 tool ghi (NFR-005). Cấu hình **không** chứa `MCP_INGEST_*DSN`.
- [ ] Chạy `semantic_synthesis` với `eval/questions.yaml` mục `phase3_questions` theo
      `docs/claude-usage/journey-3.md`; lưu kết quả vào `eval/results/`; hoàn tất review tay (NFR-003).
- [ ] `uv run mcp-ingest status` so với bound độ mới (NFR-004). **Bound chờ PO** (Open question 5);
      hiện lệnh chỉ báo số, không phán quyết.
- [ ] Spike S3 (hybrid search, T-086): sau khi có corpus thật, làm theo `docs/spikes/S3-hybrid-search.md`.
- [ ] Chạy lại `make ci` trên máy dev: xanh.

Người ký: ______  Ngày: ______
