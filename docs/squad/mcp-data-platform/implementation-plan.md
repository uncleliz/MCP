# MCP Data Platform — Implementation Plan

> Nguồn đầu vào: `requirements.md` (FR-001…FR-015, 37 AC id, NFR-001…NFR-005),
> `architecture.md` (đã reconcile với 16 ADR, 29 amendment sau design review 2026-10-01),
> `api-contract.yaml` (49 tool + 6 operation CLI, `x-readonly: true` toàn bộ), `state.json`.
> Ngôn ngữ tài liệu: tiếng Việt (`state.json.language = vi`); mọi identifier, tên file, tên
> tool, DDL, tên test bằng tiếng Anh.
>
> **Feature này là backend-only:** không có frontend, không có FE task. Toàn bộ task
> `Owner = BE`. Cấu trúc thư mục **theo đúng `architecture.md`**: uv workspace monorepo
> `packages/mcp_<source>/` với tách hai tầng `client.py` / `read_api.py` mỗi nguồn.

## Summary & sequencing strategy

Chiến lược: **vertical slice mỏng, contract-first, đi theo phase 1 → 2 → 3 của
`architecture.md`**.

1. **Contract-first là bắt buộc, không phải khuyến nghị.** `api-contract.yaml` đã có sẵn
   `inputSchema`/`outputSchema` của cả 49 tool. Mỗi task tool **không thiết kế schema**, nó chỉ
   implement đúng operation tương ứng và sinh `tools.snapshot.json` để `test_contract.py` so
   ngược lại file contract (ADR-0013). Mọi breaking change đối với contract **không phải task ở
   đây** — nó đưa flow quay lại stage `sa`.
2. **`mcp_common` đi trước mọi thứ và là điểm ảnh hưởng chung của 10 package (R11).** 10 task
   nền tảng (T-005…T-014) phải xanh trước khi mở song song 9 họ server; đổi lại, sau khi chúng
   xong thì 9 họ server **độc lập hoàn toàn** với nhau.
3. **Slice end-to-end đầu tiên được đánh dấu tường minh** (xem mục dưới): một tool duy nhất
   (`confluence_search_pages`) chạy thật từ Claude Desktop qua stdio tới Confluence và trả về
   citation. Nó khoá sớm 4 rủi ro đắt nhất cùng lúc: stdio/stdout guard (R2), envelope+citation
   (BR-002), timeout budget (NFR-002), startup credential check (ADR-0003 A1).
4. **Spike là task thật, có id, nằm ở đầu phase của nó** — không phải phụ lục: S1 trước Phase 1,
   S4 đầu Phase 2, S2 + S5 đầu Phase 3, S3 sau Phase 3. Ba spike (S2, S4, S5) là đường đóng ba
   ADR còn `proposed` (0009, 0010, 0016).
5. **Phase 1 và Phase 2 hoàn toàn stateless** (không migration, không DB) — toàn bộ công việc
   DB/pipeline dồn vào Phase 3, đúng như `architecture.md` khuyến nghị.
6. **Task read-only-enforcement không tách rời task tool.** Mỗi họ server có một task test
   `test_tools_readonly.py` riêng vì NFR-001 đòi "chạy trước sign-off mỗi phase", nhưng allowlist
   và startup credential check nằm **trong** task `client.py` của nguồn đó — chúng là code sản
   phẩm, không phải test.

### Slice end-to-end đầu tiên (walking skeleton)

**T-002 → T-005 → T-006 → T-007 → T-008 → T-009 → T-010 → T-011 → T-012 → T-014 → T-017 →
T-018 → T-019 → T-020 (chỉ `confluence_search_pages`) → T-022 → T-031.**

Điều kiện kết thúc slice: người dùng cấu hình `mcp-confluence` vào
`claude_desktop_config.json` (đoạn JSON do `mcp-common config-emit` in ra), hỏi Claude một câu,
và nhận được câu trả lời có ít nhất một URL Confluence trong mục "Nguồn"; đồng thời
`tests/test_tools_readonly.py` và `test_contract.py` của package đó xanh. Ba tool Confluence còn
lại (`get_page`, `list_spaces`, `list_page_children`) hoàn thiện ngay sau đó trong cùng T-020 —
tách ra khỏi slice để slice không bị kéo dài.

## Patterns to mirror

**Greenfield.** Repo hiện chỉ có tài liệu + skeleton rỗng; không có code sản phẩm nào để mirror:

| Đã tồn tại | Ghi chú |
|---|---|
| `packages/mcp_common/tests/__init__.py` và `packages/mcp_{gitlab,confluence,opensearch,kibana,cloudwatch,kafka,redis,sqs_sns,pgvector}/tests/__init__.py` | 10 thư mục rỗng chỉ có `tests/__init__.py`. **Chưa có `packages/mcp_ingest/`** → T-070 tạo mới. Không có `pyproject.toml`, `src/`, `uv.lock` nào. |
| `docs/adr/0001…0016` + `docs/squad/mcp-data-platform/*` | Tài liệu; 8 ADR có phần **Amendments** đè lên Decision gốc — đọc Amendments trước. |
| `CLAUDE.md` | Mô tả layout `backend/`, **lệch** với `packages/` của ADR-0001 → xem `needs_user_decision` #1 (R14). |

Vì không có code để mirror, "pattern" của dự án này là **`architecture.md` → "Cấu trúc thư mục
chuẩn của một server"** và nó là bắt buộc cho cả 9 package:

```
packages/mcp_<source>/
  pyproject.toml            # deps riêng + console_script mcp-<source>
  src/mcp_<source>/
    __init__.py  settings.py  ports.py(nếu cần)  client.py  read_api.py
    mappers.py  tools.py  prompts.py(chỉ confluence/cloudwatch/pgvector)
    server.py  cli.py  tools.snapshot.json
  tests/
    test_tools_readonly.py  test_contract.py  test_<tool>.py  test_integration.py
```

Hai bất biến về file, áp cho **mọi** task trong plan này:

- **`client.py` = tầng transport** (HTTP/SDK + `ALLOWED_OPERATIONS` + timeout). Connector của
  `mcp-ingest` dùng **đúng file này** và không bao giờ import `read_api.py` (ADR-0007 A3,
  ADR-0012 A4).
- **`read_api.py` = tầng tool** (bound `limit ≤ 100`, `max_bytes ≤ 262144`, time range ≤ 31d,
  `timeout_s ≤ 22s`). Bound **không** được đặt ở `client.py`, nếu không connector crawl toàn bộ
  nguồn sẽ bị chính bound của tool chặn.

Báo cáo spike ghi vào `docs/spikes/` (không vào `docs/squad/<feature>/` — thư mục đó thuộc các
role tài liệu).

## Setup tasks

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Done when |
|----|----|----|----|----|----|----|----|
| T-001 | BE | **Spike S1** — reachability/VPN của 5 nguồn remote | Script probe **độc lập, không phụ thuộc `mcp_common`** (httpx/boto3 thuần) kiểm Confluence, GitLab, OpenSearch, Kibana, CloudWatch từ máy dev: DNS, TCP, TLS, một endpoint GET rẻ nhất, thời gian phản hồi. Xoá script hoặc chuyển vào `doctor` sau. Là câu trả lời cho Open question 4. | NFR-002, R1; tiền đề FR-001/002/004/005/006 | — | `scripts/probe_reachability.py`, `docs/spikes/S1-reachability.md` | Bảng reachability 5 nguồn (reachable / auth-fail / unreachable + latency) đã ghi vào `docs/spikes/S1-reachability.md`, kèm kết luận nguồn nào phải hoãn integration test và phải nêu trong regression-report |
| T-002 | BE | uv workspace monorepo scaffolding | Root `pyproject.toml` với `[tool.uv.workspace] members = ["packages/*"]`, một `uv.lock`, Python 3.12+ requires; `packages/mcp_common/pyproject.toml` + `src/mcp_common/__init__.py`. Ruff + mypy config; **lint rule cấm `print()`** (R2); pytest + `pytest-asyncio` + `respx` + coverage gate 80%. | NFR-001, NFR-005 | — | `pyproject.toml`, `uv.lock`, `.python-version`, `ruff.toml`, `packages/mcp_common/{pyproject.toml,src/mcp_common/__init__.py}`, `Makefile` | `uv sync` xanh; `uv run pytest` chạy (0 test là hợp lệ); `uv run ruff check .` xanh; rule cấm `print()` báo lỗi trên một file thử |
| T-003 | BE | Dev tooling cho contract + CI | Pin `check-jsonschema` + `openapi-spec-validator` vào dev dependency (ADR-0013: máy dev **không có** spectral/redocly); một job/target chạy: lint, mypy, unit test + coverage, validate `api-contract.yaml`, và suite read-only của mọi package. | NFR-001 | T-002 | `Makefile`, `ci/pipeline.yml`, `scripts/validate_contract.py` | `make ci` chạy hết 5 bước local; `validate_contract.py` phát hiện được một lỗi schema cố ý chèn vào bản copy của contract |
| T-004 | BE | `.env.example` + bảng biến môi trường | Liệt kê đầy đủ biến chung (`MCP_HTTP_*`, `MCP_TOOL_DEADLINE`, `MCP_TOOL_DEADLINE_<TOOL>`, `MCP_MAX_OUTPUT_BYTES`, `MCP_MAX_TIME_RANGE_DAYS`, `MCP_LOG_LEVEL`, `MCP_REDACT_DISABLED`, `MCP_TRANSPORT`, `MCP_ALLOW_UNVERIFIED_CREDENTIALS`) + biến theo từng `MCP_<SOURCE>_`. **Chỉ `.env.example`, không bao giờ `.env`.** | NFR-002, NFR-005 | T-002 | `.env.example` | Mọi biến `architecture.md` mục "Config" nêu đều có mặt, có giá trị mẫu + một dòng mô tả; không có secret thật |
| T-005 | BE | `mcp_common.config` | `CommonSettings` (pydantic-settings) + loader theo env prefix `MCP_` / `MCP_<SOURCE>_`, `SecretStr`, hỗ trợ `*_FILE`, validate fail-fast → `source_misconfigured` nêu **đúng tên biến**. | NFR-005 | T-002 | `packages/mcp_common/src/mcp_common/config.py`, `packages/mcp_common/tests/test_config.py` | Unit test: thiếu biến bắt buộc → lỗi nêu tên biến; `*_FILE` đọc được; `SecretStr` không lộ trong `repr` |
| T-006 | BE | `mcp_common.logging` + **stdout guard** | Structured JSON **chỉ ra stderr**, trường chuẩn (`ts, level, server, tool, request_id, duration_ms, status, error_code, upstream_status, upstream_host, items_returned, truncated, redactions`); `MCP_LOG_FILE` tuỳ chọn; guard raise/fail test nếu có gì ghi ra stdout ngoài JSON-RPC. | NFR-005, R2 | T-005 | `.../mcp_common/logging.py`, `tests/test_logging_stdout_guard.py` | Test: `print()` trong thân tool làm test fail; toàn bộ log đi stderr; không log query đầy đủ ở INFO |
| T-007 | BE | `mcp_common.errors` — taxonomy + mapper | `ErrorCode` đủ 10 giá trị theo contract (`invalid_input`, `not_permitted`, `unauthorized`, `forbidden`, `upstream_timeout`, `upstream_unavailable`, `upstream_error`, `rate_limited`, `response_too_large`, `source_misconfigured`, `internal`) + mapper từ exception httpx/boto3/psycopg/redis/kafka. **Không** quyết định retry (thuộc `http`). | FR-014/AC-002, NFR-002 | T-005 | `.../mcp_common/errors.py`, `tests/test_errors.py` | Bảng map exception → code có test cho từng dòng; `Error`/`ErrorEnvelope` khớp `components.schemas` của contract |
| T-008 | BE | `mcp_common.envelope` — `ToolResult`/`Citation`/`Meta` | Model theo đúng `ToolResultBase`, `Citation`, `Meta`, `ItemBase` của contract. **Bất biến kiểm bằng test: `citations` khác rỗng khi `status ∈ {ok, partial}`**; `empty`/`not_found` là status thành công; mọi id là `string`. | FR-015/AC-001, FR-015/AC-002, BR-002 | T-007 | `.../mcp_common/envelope.py`, `tests/test_envelope.py` | Test dựng `ToolResult(status=ok, citations=[])` phải fail; 4 nhánh `ok/empty/not_found/error` validate được theo schema contract |
| T-009 | BE | `mcp_common.render` — text rendering + golden file | Implement đúng 6 mục của `info.x-text-rendering`: dòng status, thân item `[n]` khớp `citation_ref`, khối `<untrusted-content>`, mục `Nguồn:` dựng từ `citations[]` (không có khi `empty`/`not_found`), dòng `Lưu ý:`, dòng freshness. | FR-015/AC-001, FR-015/AC-002, FR-003/AC-002 | T-008 | `.../mcp_common/render.py`, `tests/test_render.py`, `tests/golden/*.txt` | Golden snapshot cho 4 nhánh × (list-tool, detail-tool) xanh; câu `empty` khớp **đúng** chuỗi contract quy định |
| T-010 | BE | `mcp_common.http` — client + timeout budget + transport assertion | `httpx.AsyncClient` duy nhất: connect **3s** / read **7s**, **2 lần thử (1 retry)** với 429/5xx/connect, backoff base 1s ⇒ `2×(3+7)+1 = 21s < deadline 25s`; `Retry-After` **kẹp** `min(retry_after, remaining_budget − 1s)` → vượt thì trả `rate_limited` ngay (ADR-0006 A3). **Assertion ở tầng transport: mọi request đi ra phải `GET`/`HEAD` trừ allowlist `(host, method, path)` tường minh** (ADR-0003 A2). | FR-014/AC-001, NFR-002 | T-007 | `.../mcp_common/http.py`, `tests/test_http_budget.py`, `tests/test_transport_readonly.py` | Test: endpoint chết → `upstream_timeout` sau ≈21s và **< 25s**, đúng 2 lần thử; `POST` không có trong allowlist → `NotPermittedError`; `Retry-After: 120` không sleep, trả `rate_limited` |
| T-011 | BE | `mcp_common.readonly` — allowlist guard | `enforce(allowlist, op)`, `NotPermittedError` → `not_permitted`, decorator `@readonly_tool`. Không cấp credential, chỉ chặn operation. | FR-014/AC-001, FR-014/AC-002, NFR-001 | T-007 | `.../mcp_common/readonly.py`, `tests/test_readonly_guard.py` | Operation ngoài allowlist → `not_permitted` kèm tên operation; decorator đánh dấu tool để `assert_readonly_tool_surface` quét được |
| T-012 | BE | `mcp_common.runtime` — `build_server`/`serve` + executor có biên | Dựng `FastMCP`, đăng ký tool/prompt, chọn transport (`MCP_TRANSPORT`, khác `stdio` → `source_misconfigured` fail-closed), áp deadline tổng mỗi tool call (+ override `MCP_TOOL_DEADLINE_<TOOL>`), bắt exception → envelope error, **`ThreadPoolExecutor` riêng `max_workers=4` + queue có biên** cho mọi SDK đồng bộ; queue đầy → `upstream_unavailable` ngay. **Hook `credential_check` là điều kiện serve** — `build_server()` từ chối serve nếu check fail, escape duy nhất `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` (log WARN mỗi lần khởi động). | FR-014/AC-001, FR-014/AC-002, NFR-002, NFR-005 | T-006, T-010, T-011 | `.../mcp_common/runtime.py`, `tests/test_runtime_deadline.py`, `tests/test_runtime_executor.py`, `tests/test_credential_gate.py` | Test: N call treo → call thứ N+1 trả `upstream_unavailable` **không chờ** (R16); credential check fail → `serve()` raise, không lên stdio; `MCP_TRANSPORT=http` → `source_misconfigured` |
| T-013 | BE | `mcp_common.content` + `mcp_common.redact` | `normalize` (storage-format/HTML/ADF → markdown/text), cắt theo byte + `meta.truncated`, `wrap_untrusted()` (**chỉ ở ranh giới trả kết quả, không bao giờ persist**, ADR-0015 A2); `redact` scrub secret hai chiều (log + output) + deny-glob cho key/path, `MCP_REDACT_DISABLED` là hành động tường minh. | FR-015/AC-001, R3, R4 | T-008 | `.../mcp_common/content.py`, `.../mcp_common/redact.py`, `tests/test_content.py`, `tests/test_redact.py` | Test: token/`.env`/private key bị scrub và `meta.redactions` tăng; `wrap_untrusted` không lồng hai lần; cắt byte không làm hỏng UTF-8 |
| T-014 | BE | `mcp_common.testing` — fixture kiểm chứng dùng chung | `assert_readonly_tool_surface` (quét tool surface + **phủ cả method mà `mcp-ingest` gọi**, ADR-0012 A4); fixture bật transport assertion GET/HEAD trong **mọi** unit test qua `respx`; assertion contract `x-readonly: true` / `x-side-effects: none` và `tools.snapshot.json ⊆ operation có x-interface != cli`; helper gọi **tên tool "ghi" không tồn tại** và assert **JSON-RPC error của SDK** (FR-014 AC-002 nằm ở tầng giao thức, không phải `ErrorEnvelope`); deny-regex theo tên tool **chỉ là warning** (ADR-0003 A2). | FR-014/AC-001, FR-014/AC-002, NFR-001 | T-009, T-011, T-012, T-003 | `.../mcp_common/testing.py`, `tests/test_testing_helpers.py` | Fixture bắt được 3 vi phạm cố ý: một tool POST ra ngoài allowlist, một operation thiếu `x-readonly`, một tool trong snapshot không có trong contract; helper unknown-tool assert đúng JSON-RPC error |
| T-015 | BE | Hạ tầng dev/test bằng Docker Compose | `pgvector/pgvector:pg16`, `redis:7` + **file ACL mẫu tạo user `mcp_ro` có `+acl|getuser`** (ADR-0008 A1), `apache/kafka` (KRaft single node), `localstack` (`sqs,sns`). 5 nguồn còn lại không emulate → fixture payload thật + `respx`. | NFR-001, NFR-002 | T-002 | `infra/docker-compose.yml`, `infra/redis/users.acl`, `infra/localstack/init.sh`, `docs/dev-setup.md` | `docker compose up` lên đủ 4 service; `redis-cli ACL GETUSER mcp_ro` chạy được bằng chính user đó; LocalStack có 1 queue + 1 topic mẫu |
| T-016 | BE | `mcp-common config-emit` + tài liệu dùng trong Claude | Subcommand in đoạn JSON dán vào `claude_desktop_config.json` cho từng server (phục vụ verification NFR-005) + `docs/claude-usage/` (bật server theo phase, ngân sách context 49 tool, R5) + snippet CLAUDE.md về kỷ luật citation (ADR-0014). | NFR-005, FR-015/AC-001, FR-015/AC-002 | T-005 | `.../mcp_common/cli.py`, `docs/claude-usage/README.md` | `uv run mcp-common config-emit --server confluence` in ra JSON hợp lệ dán được; tài liệu nêu rõ khuyến nghị không bật cả 9 server cùng lúc |

## Tasks

### Phase 1 — Confluence + GitLab (FR-001, FR-002, FR-003)

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Done when |
|----|----|----|----|----|----|----|----|
| T-017 | BE | `mcp-confluence` package skeleton + settings | `pyproject.toml` (console_script `mcp-confluence`), `settings.py` với `env_prefix="MCP_CONFLUENCE_"` (base URL, token, **biến chọn Cloud vs Server/DC**), `server.py` gọi `build_server()`, `cli.py` (`serve`, `doctor`, `tools-dump`). | FR-001 | T-012, T-005 | `packages/mcp_confluence/{pyproject.toml,src/mcp_confluence/{__init__,settings,server,cli}.py}` | `uv run mcp-confluence --help` chạy; `serve` lên stdio và trả `tools/list` rỗng; `doctor` chạy được |
| T-018 | BE | Confluence `client.py` — transport + allowlist + startup check | httpx client dùng `mcp_common.http`; `ALLOWED_OPERATIONS` chỉ GET (`/rest/api/content/search`, `/content/{id}`, `/space`, `/content/{id}/child/page`, + 1 endpoint current-user **chỉ cho startup check, không expose thành tool**); **`body.export_view` bị loại khỏi allowlist** (render macro phía server = side effect quan sát được, ADR-0007 A1); con trỏ phân trang tường minh; timeout theo bộ số 0006 A2. Startup check: current-user + assert token không tạo được nội dung. | FR-001/AC-003, FR-014/AC-001, NFR-002 | T-017, T-010, T-011 | `.../src/mcp_confluence/client.py` | Unit test respx: request ngoài allowlist → `not_permitted`; `body.export_view` bị từ chối ở tầng code; startup check fail → không serve |
| T-019 | BE | Confluence `read_api.py` + `mappers.py` | Bound của tool (`limit ≤ 100`, `max_chars`/`max_bytes`, `updated_after` ISO-8601 có tz) + normalize storage/view → markdown qua `mcp_common.content` + map payload → `ConfluencePage`/`ConfluenceSpace`/`ConfluenceChildPage` + dựng `Citation{source_type, label, uri, locator{page_id, version}}`. | FR-001/AC-001, FR-015/AC-001 | T-018, T-013 | `.../read_api.py`, `.../mappers.py` | Mapper test theo fixture payload thật: `uri` là URL mở được, `locator` có `page_id` + `version`; bound vi phạm → `invalid_input` nêu field |
| T-020 | BE | Confluence — 4 tool + `tools.snapshot.json` | `confluence_search_pages` (làm **trước tiên**, là walking skeleton), `confluence_get_page`, `confluence_list_spaces`, `confluence_list_page_children`; `outputSchema` theo contract; nhánh `empty` (không khớp) và `not_found` (page_id không tồn tại) là **status**, không phải exception. | FR-001/AC-001, FR-001/AC-002, FR-015/AC-001 | T-019, T-008, T-009 | `.../tools.py`, `.../tools.snapshot.json` | 4 tool xuất hiện trong `tools/list`; snapshot khớp 4 operation `/mcp/confluence/...` của contract; test nhánh `empty` trả `items: []`, `citations: []`, `meta.query_echo` |
| T-021 | BE | Prompt `dev_knowledge_lookup` + server instructions | Prompt MCP hướng Claude gọi **cả** Confluence và GitLab rồi tổng hợp, và **bắt buộc nói rõ khi một nguồn trả `empty`** (ADR-0014). Server instructions nhắc kỷ luật citation. | FR-003/AC-001, FR-003/AC-002 | T-020 | `.../prompts.py` | `prompts/list` trả `dev_knowledge_lookup` với argument `question`; text prompt chứa chỉ thị "nêu rõ nguồn không có dữ liệu" |
| T-022 | BE | `mcp-confluence` — bộ test đầy đủ | `test_tools_readonly.py` (surface + transport GET/HEAD + assertion contract + unknown write tool ở tầng JSON-RPC), `test_contract.py` (snapshot + 4 nhánh theo `api-contract.yaml`), `test_<tool>.py` với respx theo fixture thật, `test_integration.py` gắn `@pytest.mark.live`. | FR-001/AC-001, FR-001/AC-002, FR-001/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-020, T-014 | `packages/mcp_confluence/tests/*`, `tests/fixtures/confluence/*.json` | Toàn bộ test xanh (live skip nếu S1 báo unreachable); coverage ≥ 80% trên code đã đổi |
| T-023 | BE | `mcp-gitlab` skeleton + `client.py` + startup PAT scope check | Package + `settings.py` (`MCP_GITLAB_*`, `MCP_GITLAB_PATH_DENY`); `client.py` chỉ GET `/api/v4/...` trong allowlist + startup check `GET /api/v4/personal_access_tokens/self` assert `scopes ⊆ {read_api, read_repository}` (ADR-0007 A2); deny-glob path áp ở **connector-facing layer** để dùng lại được ở Phase 3. | FR-002/AC-003, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_gitlab/{pyproject.toml,src/mcp_gitlab/{settings,client,server,cli}.py}` | PAT có scope `api` → **từ chối serve**; request POST/PUT/DELETE bị transport assertion chặn; deny-glob chặn `.env`/`*.pem` |
| T-024 | BE | GitLab `read_api.py` + `mappers.py` | Bound của tool + map sang `GitLabProject`, `GitLabCodeHit`, `GitLabFile`, `GitLabTreeEntry`, `GitLabCommit`, `GitLabMergeRequest`, `GitLabIssue`, `GitLabPipeline`, `GitLabJob`, `GitLabNote`, `GitLabChangedFile` + `Citation` là URL web (không phải URL API). | FR-002/AC-001, FR-015/AC-001 | T-023, T-013 | `.../read_api.py`, `.../mappers.py` | Mapper test mọi schema GitLab của contract; `citations[].uri` là link web mở được; nội dung file đi qua redaction |
| T-025 | BE | GitLab tool nhóm A — project & code | `gitlab_search_projects`, `gitlab_search_code`, `gitlab_get_file`, `gitlab_list_repository_tree`. `not_found` tường minh khi project/path/ref không tồn tại. | FR-002/AC-001, FR-002/AC-002 | T-024, T-008, T-009 | `.../tools.py` | 4 tool khớp contract; test: project không tồn tại → `status=not_found`, không phải error; file bị deny-glob → `not_permitted` |
| T-026 | BE | GitLab tool nhóm B — commit & merge request | `gitlab_list_commits`, `gitlab_list_merge_requests`, `gitlab_get_merge_request` (kèm note/changed file theo contract). Lưu ý: hai tool có chữ `merge` trong tên là **hợp lệ** — deny-regex theo tên tool đã bị hạ xuống warning (ADR-0003 A2). | FR-002/AC-001, FR-002/AC-002 | T-024 | `.../tools.py` | 3 tool khớp contract; `test_tools_readonly` **không** fail vì tên tool chứa `merge`; MR id không tồn tại → `not_found` |
| T-027 | BE | GitLab tool nhóm C — issue & pipeline | `gitlab_list_issues`, `gitlab_get_issue`, `gitlab_list_pipelines`, `gitlab_get_pipeline` + `tools.snapshot.json` cho cả 11 tool. | FR-002/AC-001, FR-002/AC-002 | T-025, T-026 | `.../tools.py`, `.../tools.snapshot.json` | `tools/list` đúng 11 tool; snapshot khớp 11 operation `/mcp/gitlab/...`; ≤ 12 tool/server (R5) |
| T-028 | BE | `mcp-gitlab` — bộ test đầy đủ | Như T-022 nhưng cho GitLab, cộng test âm cho mọi thao tác ghi điển hình (create issue, merge, push) và test deny-glob. | FR-002/AC-001, FR-002/AC-002, FR-002/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-027, T-014 | `packages/mcp_gitlab/tests/*`, `tests/fixtures/gitlab/*.json` | Test xanh; không tool nào cho phép ghi; coverage ≥ 80% trên code đã đổi |
| T-029 | BE | Bộ câu hỏi eval Phase 1 (Journey 1) | `eval/questions.yaml` với `expected_sources` + `expected_behavior` cho Journey 1 (gồm **case một nguồn rỗng**) + script chạy tay ghi lại câu trả lời để review theo NFR-003. | FR-003/AC-001, FR-003/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-003 | T-021, T-028 | `eval/questions.yaml`, `scripts/run_eval.py`, `docs/claude-usage/journey-1.md` | ≥ 10 câu hỏi Phase 1; chạy thật cho ra bảng có/không citation; case nguồn rỗng cho ra câu "không tìm thấy tài liệu Confluence cho …" |
| T-030 | BE | Kiểm chứng NFR-002 cho Phase 1 | Test giả lập endpoint không tới được cho cả hai server: assert (a) code `upstream_timeout`/`upstream_unavailable`, (b) tổng thời gian ≈ 21s và **< 25s**, (c) **đúng 2 lần thử**, (d) `hint` nhắc VPN. Tool có `MCP_TOOL_DEADLINE_<TOOL>` riêng assert theo deadline đó. | NFR-002, FR-001/AC-002, FR-002/AC-002 | T-022, T-028 | `packages/mcp_confluence/tests/test_timeout_budget.py`, `packages/mcp_gitlab/tests/test_timeout_budget.py` | 4 assertion trên xanh; ngưỡng số **phải khớp NFR-002 threshold do PO chốt** — đến khi đó dùng bộ số ADR-0006 A2 và ghi chú trong test |
| T-031 | BE | Sign-off Phase 1 | Chạy `doctor` cả hai server; đăng ký vào Claude Desktop/Code bằng JSON của `config-emit` và xác nhận tool list hiện lên + phản hồi (NFR-005); chạy toàn bộ suite read-only (NFR-001); ghi checklist sign-off. | NFR-001, NFR-005, FR-014/AC-001, FR-014/AC-002 | T-029, T-030 | `docs/signoff/phase-1.md` | Checklist đủ: 15 tool (4+11) hiện trong Claude, 0 tool ghi, suite read-only xanh, `doctor` xanh hoặc lý do VPN đã ghi |

### Phase 2 — OpenSearch, Kibana, CloudWatch, Kafka, Redis (FR-004…FR-009)

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Done when |
|----|----|----|----|----|----|----|----|
| T-032 | BE | **Spike S4** — Kafka client install check | Thử cài `confluent-kafka` (extension C) trên môi trường dev thật; nếu fail thì đo lại với `kafka-python`. So AdminClient: metadata, config, consumer-group lag, `assign()`, no-commit. **Đầu ra: chốt client và đóng ADR-0009 (`proposed` → `accepted`).** | FR-007, R7; đóng needs_user_decision #2 | T-002 | `docs/spikes/S4-kafka-client.md`, `packages/mcp_kafka/pyproject.toml` (chỉ dòng dependency) | Báo cáo nêu client được chọn + lý do + kết quả cài; dependency đã pin trong `pyproject.toml`; ADR-0009 được SA đóng |
| T-033 | BE | `mcp-opensearch` skeleton + `client.py` + startup check | `opensearch-py` async; allowlist endpoint (`_cat/indices`, `_mapping`, `_search`, `_count`); **từ chối `script`/`scripted_metric`/`runtime_mappings`/`scroll`/`point_in_time`** (ADR-0008 A2 — `scroll`/PIT tạo state trên cluster); phân trang sâu bằng `search_after`; startup check quyền read-only. | FR-004/AC-002, FR-014/AC-001, NFR-002 | T-012, T-010, T-011, T-032 | `packages/mcp_opensearch/{pyproject.toml,src/mcp_opensearch/{settings,client,server,cli}.py}` | Body chứa `script` hoặc `pit` → `not_permitted`; startup check fail → không serve; timeout theo budget 0006 A2 |
| T-034 | BE | OpenSearch `read_api.py` + `mappers.py` | Bound (`limit ≤ 100`, time range bắt buộc và ≤ `MCP_MAX_TIME_RANGE_DAYS`, `max_bytes`) + map sang `OpenSearchIndex`/`OpenSearchMapping`/`OpenSearchDocument`/`OpenSearchCount`/`OpenSearchBucket` + `Citation{index, _id, @timestamp}`. | FR-004/AC-001, FR-015/AC-001 | T-033, T-013 | `.../read_api.py`, `.../mappers.py` | `time_from > time_to` → `invalid_input`; citation có index + document id + timestamp |
| T-035 | BE | OpenSearch tool nhóm A | `opensearch_list_indices`, `opensearch_get_mapping`, `opensearch_search_logs`. | FR-004/AC-001, FR-004/AC-002 | T-034, T-008, T-009 | `.../tools.py` | 3 tool khớp contract; index không tồn tại → `not_found`; 0 hit → `empty` |
| T-036 | BE | OpenSearch tool nhóm B | `opensearch_count`, `opensearch_aggregate`, `opensearch_search_dsl` (validate body theo allowlist cấu trúc; dùng `MCP_TOOL_DEADLINE_OPENSEARCH_SEARCH_DSL` cho query chậm hợp lệ) + `tools.snapshot.json`. | FR-004/AC-001, FR-004/AC-002, FR-014/AC-001 | T-035 | `.../tools.py`, `.../tools.snapshot.json` | 6 tool trong snapshot khớp contract; `search_dsl` với `script` bị chặn; `timeout_s` validate theo deadline riêng |
| T-037 | BE | `mcp-opensearch` — bộ test đầy đủ | readonly + contract + unit (respx/mock client) + integration `@pytest.mark.live`; test riêng cho `scroll`/PIT/`script` bị chặn. | FR-004/AC-001, FR-004/AC-002, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-036, T-014 | `packages/mcp_opensearch/tests/*` | Test xanh; coverage ≥ 80% code đã đổi |
| T-038 | BE | `mcp-kibana` skeleton + `client.py` + startup check | Thin REST client cho 3 endpoint GET (`_find`, get saved object, và space/status cho startup check); header `kbn-xsrf` không bao giờ dùng cho method ghi. | FR-005, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_kibana/{pyproject.toml,src/mcp_kibana/{settings,client,server,cli}.py}` | Chỉ 3 endpoint GET trong allowlist; startup check xanh/đỏ đúng; timeout theo budget |
| T-039 | BE | Kibana — 3 tool + deep link đúng khung thời gian | `kibana_find_saved_objects`, `kibana_get_saved_object`, `kibana_build_dashboard_link` (dựng `_g=(time:(from,to))` từ input, **không gọi mạng**) + mappers + snapshot. | FR-005/AC-001, FR-005/AC-002, FR-015/AC-001 | T-038, T-008, T-009 | `.../{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | Link sinh ra mở đúng dashboard + đúng time range; không khớp dashboard → `not_found`; 3 tool khớp contract |
| T-040 | BE | `mcp-kibana` — bộ test đầy đủ | readonly + contract + unit respx + live. | FR-005/AC-001, FR-005/AC-002, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-039, T-014 | `packages/mcp_kibana/tests/*` | Test xanh; coverage ≥ 80% code đã đổi |
| T-041 | BE | `mcp-cloudwatch` skeleton + boto3 `client.py` + IAM startup check | boto3 qua `asyncio.to_thread` **trên executor có biên của runtime** (ADR-0006 A1); allowlist API `Describe*/Get*/List*/Filter*/StartQuery`; timeout `connect 3 / read 7 / max_attempts 2`; startup check `sts:GetCallerIdentity` + (nếu được phép) `iam:SimulatePrincipalPolicy` cho một action ghi phải trả `implicitDeny` (ADR-0008 A3). | FR-006, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_cloudwatch/{pyproject.toml,src/mcp_cloudwatch/{settings,client,server,cli}.py}` | API ngoài allowlist → `not_permitted`; SimulatePrincipalPolicy trả `allowed` cho action ghi → **từ chối serve**; call boto3 chạy trên executor có biên |
| T-042 | BE | CloudWatch tool nhóm logs | `cloudwatch_list_log_groups`, `cloudwatch_filter_log_events`, `cloudwatch_run_logs_insights` — `StartQuery` phải có **`StopQuery` trong `try/finally` + `asyncio.shield`** (ADR-0006 A3 / 0008 A4); dùng `MCP_TOOL_DEADLINE_CLOUDWATCH_RUN_LOGS_INSIGHTS`. | FR-006/AC-001, FR-006/AC-002 | T-041, T-008, T-009 | `.../{read_api,mappers,tools}.py` | Log group không tồn tại → `not_found`; test: huỷ giữa Insights vẫn gọi `StopQuery` đúng một lần; citation có log group + time range |
| T-043 | BE | CloudWatch tool nhóm metrics & alarms | `cloudwatch_list_metrics`, `cloudwatch_get_metric_data`, `cloudwatch_describe_alarms`, `cloudwatch_describe_alarm_history` + snapshot 7 tool. | FR-006/AC-001, FR-006/AC-002 | T-042 | `.../tools.py`, `.../tools.snapshot.json` | 7 tool khớp contract; metric không tồn tại → `empty`/`not_found` đúng ngữ nghĩa contract; citation có metric/alarm name + window |
| T-044 | BE | Prompt `incident_investigation` | Prompt MCP (argument `service`, `time_from`, `time_to`) điều phối CloudWatch → OpenSearch → Kibana, tuỳ chọn Kafka/Redis, và **bắt buộc nêu thành một dòng mỗi nguồn trả `empty`** ("không có alarm CloudWatch trong khung giờ này"). | FR-009/AC-001, FR-009/AC-002 | T-043, T-036, T-039 | `packages/mcp_cloudwatch/src/mcp_cloudwatch/prompts.py` | `prompts/list` trả prompt với 3 argument; text chứa chỉ thị nêu gap và chỉ thị citation từng mệnh đề |
| T-045 | BE | `mcp-cloudwatch` — bộ test đầy đủ | readonly + contract + unit (botocore stubber) + live; test executor cạn (R16) cho nguồn đồng bộ này. | FR-006/AC-001, FR-006/AC-002, FR-014/AC-001, FR-014/AC-002, NFR-001, NFR-002 | T-044, T-014 | `packages/mcp_cloudwatch/tests/*` | Test xanh; N call treo → call N+1 trả `upstream_unavailable`; coverage ≥ 80% code đã đổi |
| T-046 | BE | `mcp-kafka` — `ports.py` + `client.py` read-only + startup assert | `KafkaReader` port trong `ports.py` (đổi implementation không lan ra tool layer, R7); adapter theo client S4 chốt: `assign()` **không** `subscribe`, `enable.auto.commit=false`, **`allow.auto.create.topics=false`**, **không bao giờ truyền `topic=` vào metadata request**, không `Producer` nào được khởi tạo; `socket.timeout.ms`/`metadata.request.timeout.ms` = 8000; chạy trên executor có biên. Startup assert cấu hình (+ `describe_acls` nếu được phép). | FR-007/AC-003, FR-014/AC-001, NFR-002, R17 | T-032, T-012, T-011 | `packages/mcp_kafka/{pyproject.toml,src/mcp_kafka/{settings,ports,client,server,cli}.py}` | Test: không có đường code nào tạo `Producer`; `allow.auto.create.topics` = false được assert lúc khởi động; đổi adapter sang client còn lại không sửa `tools.py` |
| T-047 | BE | Kafka — 5 tool | `kafka_list_topics`, `kafka_describe_topic`, `kafka_peek_messages` (assign + seek + no commit), `kafka_list_consumer_groups`, `kafka_describe_consumer_group` (lag) + mappers + snapshot. Citation = topic + partition + offset (offset là **string**). | FR-007/AC-001, FR-007/AC-002, FR-015/AC-001 | T-046, T-008, T-009 | `.../{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | 5 tool khớp contract; `peek` không dịch offset của consumer group khác; topic không tồn tại → `not_found` |
| T-048 | BE | `mcp-kafka` — bộ test đầy đủ + test âm R17 | readonly + contract + unit + integration trên Kafka KRaft của compose; **test then chốt: gọi `kafka_describe_topic` với topic không tồn tại trên broker có `auto.create.topics.enable=true` và assert topic KHÔNG được tạo** (chính test âm FR-007 AC-002 từng là một thao tác ghi). | FR-007/AC-001, FR-007/AC-002, FR-007/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-047, T-014, T-015 | `packages/mcp_kafka/tests/*` | Test âm R17 xanh: `_cat` topic list trước/sau giống nhau; không tool ghi nào tồn tại; coverage ≥ 80% code đã đổi |
| T-049 | BE | `mcp-redis` skeleton + `client.py` + ACL startup check | `redis.asyncio`; allowlist command (`SCAN`, `GET`, `TYPE`, `TTL`, `MEMORY USAGE`, `INFO`, `ACL WHOAMI/GETUSER`) — **`KEYS` bị cấm**, dùng `SCAN`; connect 2s / read 5s; startup check `ACL WHOAMI` + **`ACL GETUSER` assert không có category ghi** (đòi user có `+acl|getuser`, ADR-0008 A1). | FR-008/AC-003, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_redis/{pyproject.toml,src/mcp_redis/{settings,client,server,cli}.py}` | Lệnh ngoài allowlist → `not_permitted`; user có quyền ghi → **từ chối serve**; `KEYS` không tồn tại trong bất kỳ đường code nào |
| T-050 | BE | Redis — 4 tool + decode giá trị | `redis_scan_keys`, `redis_get_key`, `redis_key_info`, `redis_server_info`; decode `auto|json|utf8|base64`; **redaction + deny-glob key nhạy cảm** (R4); mappers + snapshot. | FR-008/AC-001, FR-008/AC-002, FR-015/AC-001 | T-049, T-013, T-008 | `.../{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | 4 tool khớp contract; key không tồn tại → `not_found` (nil tường minh); giá trị chứa token bị scrub, `meta.redactions > 0` |
| T-051 | BE | `mcp-redis` — bộ test đầy đủ | readonly + contract + unit + integration trên `redis:7` của compose với user `mcp_ro`; test âm cho `SET`/`DEL`/`EXPIRE`. | FR-008/AC-001, FR-008/AC-002, FR-008/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-050, T-014, T-015 | `packages/mcp_redis/tests/*` | Test xanh; mọi lệnh ghi bị từ chối ở cả code và ACL server; coverage ≥ 80% code đã đổi |
| T-052 | BE | Bộ câu hỏi eval Phase 2 (Journey 2) | Mở rộng `eval/questions.yaml` cho incident investigation, gồm case **một nguồn rỗng trong khung giờ** và case nghi Kafka lag/Redis cache; chạy tay qua prompt `incident_investigation`. | FR-009/AC-001, FR-009/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-003 | T-044, T-037, T-040, T-045, T-048, T-051 | `eval/questions.yaml`, `docs/claude-usage/journey-2.md` | ≥ 10 câu hỏi Phase 2; câu trả lời có log/metric excerpt + dashboard link, mỗi mệnh đề một citation; case rỗng nêu gap thành một dòng |
| T-053 | BE | Sign-off Phase 2 | `doctor` 5 server; đăng ký Claude Desktop (NFR-005); suite read-only toàn bộ (NFR-001); kiểm chứng NFR-002 + test executor cạn cho 5 server (trong đó CloudWatch/Kafka là SDK đồng bộ). | NFR-001, NFR-002, NFR-005, FR-014/AC-001, FR-014/AC-002 | T-052, T-030 | `docs/signoff/phase-2.md`, `packages/mcp_{opensearch,kibana,cloudwatch,kafka,redis}/tests/test_timeout_budget.py` | Checklist đủ: 25 tool Phase 2 (6+3+7+5+4) hiện trong Claude, tổng 40 tool sau Phase 1+2, 0 tool ghi, timeout ≈21s < 25s ở mọi nguồn |

### Phase 3 — SQS/SNS, pgvector query server, ingest/embedding pipeline (FR-010…FR-013)

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Done when |
|----|----|----|----|----|----|----|----|
| T-054 | BE | **Spike S2** — embedding bake-off | Bake-off `bge-m3` vs `multilingual-e5-large` (cùng 1024d ⇒ không cần migrate schema) trên bộ câu hỏi NFR-003 **trước khi embed toàn bộ**; đo recall@k, latency CPU, RAM, thời gian tải model. **Đầu ra: chốt provider/model, đóng ADR-0010.** | FR-011, FR-012, R6; đóng needs_user_decision #3 | T-029 (bộ câu hỏi), T-002 | `docs/spikes/S2-embedding-bakeoff.md`, `scripts/bakeoff_embedding.py` | Bảng so sánh 2 model + model được chọn + `model_id`/`dimensions` chốt; ADR-0010 được SA đóng |
| T-055 | BE | **Spike S5** — quy tắc `visibility` + `source_id` per connector | Định nghĩa quy tắc suy ra `documents.visibility` (`team`/`restricted`) cho Confluence (space permission), GitLab (project visibility + member role), OpenSearch; và quy tắc dựng `source_id` **ổn định qua ILM rollover** (ADR-0012 A5). **Chạy sau khi PO trả lời Open question 3.** Đầu ra đóng ADR-0016 (R15). | FR-012/AC-001, FR-012/AC-003, BR-003, BR-005; đóng needs_user_decision #5 | T-001; **Gate B: quyết định RBAC (ADR-0016 Phần 2)** | `docs/spikes/S5-visibility-source-id.md` | Bảng quy tắc per-connector, có case "quyền đổi ở nguồn sau khi crawl"; `source_id` có test ví dụ cho index rollover; ADR-0016 được SA đóng |
| T-056 | BE | Migration 0001–0005 + `mcp-ingest db upgrade` | `packages/mcp_ingest` skeleton (Typer) + runner migration SQL đánh số theo dõi bằng `kb.schema_migrations`; `0001_extensions.sql` (vector, pgcrypto), `0002_schema_kb.sql` (`documents`, `chunks` + unique `(source_type, source_id)` và `(document_id, chunk_index)`), `0003_indexes.sql` (HNSW `vector_cosine_ops` m=16 ef_construction=64 + btree), `0004_ingest_state.sql`, `0005_roles.sql` (`mcp_ingest_rw`, `mcp_query_ro` với `default_transaction_read_only=on`). | FR-012/AC-001, FR-011/AC-003 | T-054 (chốt `dimensions`), T-015 | `packages/mcp_ingest/{pyproject.toml,src/mcp_ingest/{__init__,settings,cli,db}.py,migrations/0001…0005.sql}` | `mcp-ingest db upgrade` áp được từ DB rỗng lên 0005 và **idempotent** khi chạy lại; `mcp_query_ro` không INSERT được (test khẳng định) |
| T-057 | BE | Migration 0006 — review follow-up | `0006_review_followup.sql`: `documents.last_seen_run_id`, `last_seen_at`, `chunk_config_hash`, `visibility text NOT NULL DEFAULT 'team'`, index `documents(source_type, last_seen_run_id)`, bảng `kb.ingest_failures` (PK `(source_type, source_id)`, `attempts`, `stage`, `code`, `last_error`). | FR-012/AC-002, FR-012/AC-003 | T-056 | `packages/mcp_ingest/migrations/0006_review_followup.sql` | Schema sau upgrade khớp **đúng** ER diagram của `architecture.md`; test: câu tombstone scoped theo `last_seen_run_id` chạy được |
| T-058 | BE | `EmbeddingProvider` port + 2 adapter + validate model/dimension | Port trong `mcp_ingest/ports.py` (`embed_documents`, `embed_query`, `model_id`, `dimensions`, `max_input_tokens`, `normalize`) + `LocalSentenceTransformerProvider` (load lazy một lần/process) + `HttpEmbeddingProvider` (OpenAI-compatible). Module embedding **không được import psycopg** để `mcp-pgvector` dùng lại mà không kéo theo đường ghi (xem open question #1). | FR-011/AC-001, FR-012/AC-001 | T-054 | `packages/mcp_ingest/src/mcp_ingest/{ports.py,embedding/{__init__,local,http}.py}`, `tests/test_embedding.py` | 2 adapter pass cùng bộ test contract của port; **import-linter test: `mcp_ingest.embedding` không import `psycopg`**; `model_id`/`dimensions` khác dữ liệu đã lưu → lỗi yêu cầu re-embed |
| T-059 | BE | `mcp-sqs-sns` skeleton + `client.py` + startup check | boto3 trên executor có biên; allowlist **chỉ** `ListQueues`, `GetQueueAttributes`, `GetQueueUrl`, `ListDeadLetterSourceQueues`, `ListTopics`, `GetTopicAttributes`, `ListSubscriptionsByTopic` — **không `ReceiveMessage`, không `SendMessage`, không `Publish`, không `DeleteMessage`**; startup check IAM như T-041. | FR-010/AC-003, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_sqs_sns/{pyproject.toml,src/mcp_sqs_sns/{settings,client,server,cli}.py}` | API ngoài allowlist → `not_permitted`; test khẳng định không đường code nào gọi `ReceiveMessage` (nó thay đổi visibility timeout ⇒ side effect) |
| T-060 | BE | SQS/SNS — 6 tool | `sqs_list_queues`, `sqs_get_queue_attributes`, `sqs_list_dead_letter_source_queues`, `sns_list_topics`, `sns_get_topic_attributes`, `sns_list_subscriptions_by_topic` + mappers (`SqsQueue*`, `SnsTopic*`, `SnsSubscription`) + snapshot. Citation = queue/topic name hoặc ARN. | FR-010/AC-001, FR-010/AC-002, FR-015/AC-001 | T-059, T-008, T-009 | `.../{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | 6 tool khớp contract; queue/topic không tồn tại → `not_found`; `ApproximateNumberOfMessages` + ARN có trong item/citation |
| T-061 | BE | `mcp-sqs-sns` — bộ test đầy đủ | readonly + contract + unit (stubber) + integration trên LocalStack; test âm send/publish/delete. | FR-010/AC-001, FR-010/AC-002, FR-010/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-060, T-014, T-015 | `packages/mcp_sqs_sns/tests/*` | Test xanh; số message trên LocalStack không đổi trước/sau khi chạy suite; coverage ≥ 80% code đã đổi |
| T-062 | BE | `mcp-pgvector` skeleton + `client.py` + startup credential check | psycopg 3 + `pgvector[psycopg]`, role `mcp_query_ro`, **mọi transaction `BEGIN READ ONLY`**, `connect_timeout=3`, `statement_timeout=15s`; SQL tham số hoá viết sẵn (**không** có tool `postgres_query(sql)`); startup check `SHOW transaction_read_only = on` **và** `has_table_privilege('kb.chunks','INSERT') = false` **và** `embedding_model` trong dữ liệu khớp cấu hình (ADR-0003 A1, ADR-0008 A3, ADR-0010). | FR-011/AC-003, FR-014/AC-001, NFR-002 | T-056, T-057, T-058, T-012 | `packages/mcp_pgvector/{pyproject.toml,src/mcp_pgvector/{settings,client,server,cli}.py}` | **Dán DSN `mcp_ingest_rw` vào `MCP_PGVECTOR_DSN` → từ chối serve** (đây là chính lỗ hổng ADR-0003 A1 vá); model lệch dữ liệu → từ chối serve với thông báo yêu cầu re-embed |
| T-063 | BE | `kb_semantic_search` | Embed câu hỏi bằng **cùng** provider; `SET LOCAL hnsw.iterative_scan=relaxed_order` + `hnsw.ef_search=GREATEST(64, 8*top_k)`; `ORDER BY embedding <=> $1`, **`deleted_at IS NULL` là filter bắt buộc**; filter `source_types`/`container`/`updated_after`. **`status=empty` chỉ khi similarity tốt nhất *không tính filter* dưới `min_similarity`**; nếu bộ lọc loại hết thì `empty` + `meta.warnings` nói rõ "bộ lọc đã loại N kết quả" (ADR-0011 A3). Fallback over-fetch `top_k × 4` nếu pgvector < 0.8. | FR-011/AC-001, FR-011/AC-002, FR-015/AC-001, FR-015/AC-002 | T-062 | `.../{read_api,mappers,tools}.py` | Khớp operation contract; test: có chunk rất giống nhưng bị filter loại → `empty` **kèm warning phân biệt được với "không có gì giống"**; `top_k ≤ 50` |
| T-064 | BE | `kb_get_document` + `kb_list_sources` | `kb_get_document` (theo `document_id` hoặc `(source_type, source_id)`, trả `KbDocument` + chunk); `kb_list_sources` trả `KbSourceStat` với `last_ingested_at`, doc/chunk count, `staleness_hours` → `meta.data_freshness` để Claude tuyên bố độ mới (NFR-004). `wrap_untrusted` áp **ở đây**, không lấy từ DB (ADR-0015 A2). | FR-011/AC-001, NFR-004, FR-015/AC-001 | T-063 | `.../tools.py`, `.../tools.snapshot.json` | 3 tool khớp contract; document đã tombstone → `not_found`; `kb_list_sources` trên DB rỗng → `empty`, không lỗi |
| T-065 | BE | Prompt `semantic_synthesis` | Prompt MCP điều phối `kb_semantic_search` → (tuỳ chọn) `sqs_get_queue_attributes`, **bắt buộc trích dẫn URL gốc phía sau embedding** (không phải `document_id`) và **bắt buộc nói rõ khi không có dữ liệu index nào liên quan**. | FR-013/AC-001, FR-013/AC-002 | T-064, T-060 | `packages/mcp_pgvector/src/mcp_pgvector/prompts.py` | `prompts/list` trả prompt; text chứa chỉ thị dùng `source_uri` gốc + chỉ thị nêu "không tìm thấy dữ liệu đã index" + nhắc độ mới từ `data_freshness` |
| T-066 | BE | `mcp-pgvector` — bộ test đầy đủ + mốc recall NFR-003 | readonly + contract + unit + integration trên pgvector của compose; **benchmark recall ≥ 0.95 so với brute-force (`SET enable_indexscan=off`) trên bộ 50 truy vấn mẫu** (ADR-0011 A3); test `mcp_query_ro` không ghi được. | FR-011/AC-001, FR-011/AC-002, FR-011/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001, NFR-003 | T-064, T-014, T-075 (dữ liệu mẫu nạp qua pipeline) | `packages/mcp_pgvector/tests/*`, `eval/recall_queries.yaml` | Recall ≥ 0.95 trên 50 truy vấn; mọi INSERT/UPDATE/DELETE qua server này bị từ chối; coverage ≥ 80% code đã đổi |
| T-067 | BE | **(Có điều kiện — Gate B)** Filter `visibility` + RBAC cho Phase 3 | **Chỉ chạy nếu PO chọn "corpus được chứa `restricted`"** ⇒ RBAC per-user là must-have của Phase 3: identity per-request, mapping identity → nhóm quyền, filter `visibility` trong **mọi** truy vấn FR-011. Nếu PO chọn "không" thì task này **không tồn tại** và thay bằng T-073 (ingest từ chối `visibility != 'team'`). | FR-011/AC-001, BR-003, ADR-0016 Phần 2 | T-063, T-055; **Gate B: needs_user_decision #4** | `packages/mcp_pgvector/src/mcp_pgvector/{read_api,client}.py`, `docs/adr/0016-*.md` (SA cập nhật) | Hoặc: filter `visibility` có test cho cả hai nhóm quyền; hoặc: task bị đóng với lý do "PO chọn corpus đồng nhất quyền" và T-073 thay thế |
| T-068 | BE | `mcp-ingest` — CLI shell + report schema + exit code | Typer app với 6 lệnh theo contract (`db upgrade`, `run`, `status`, `sources`, `reembed`, `prune`); model `IngestRunReport`/`IngestSourceResult`/`IngestError`/`MigrationResult`/`IngestStatusRow`/`IngestSourceConfigRow` khớp `components.schemas`; **exit code 0 success / 1 partial / 2 failed**; log JSON ra stderr. | FR-012/AC-001, FR-012/AC-002 | T-056, T-006 | `packages/mcp_ingest/src/mcp_ingest/cli.py`, `.../reports.py`, `tests/test_cli_contract.py` | 6 lệnh `--help` chạy; output `--json` validate được theo 6 operation `x-interface: cli` của contract; exit code có test cho cả 3 giá trị |
| T-069 | BE | Khung `SourceConnector` + registry + lệnh `sources` | Protocol `iter_documents(cursor, mode) -> SourceDocument{source_id, source_uri, raw_content, source_updated_at, visibility, …}`; registry connector; lệnh `sources` liệt kê connector + trạng thái cấu hình. **Connector dùng `client.py` của package nguồn, không bao giờ `read_api.py`** (ADR-0012 A4). | FR-012/AC-001, FR-014/AC-001 | T-068, T-014 | `.../connectors/{__init__,base,registry}.py`, `tests/test_connector_isolation.py` | `mcp-ingest sources` in đủ connector đã đăng ký; **test: không module connector nào import `read_api`**; `assert_readonly_tool_surface` phủ method mà connector gọi |
| T-070 | BE | Connector Confluence | Dùng `mcp_confluence.client`, crawl incremental theo watermark `lastModified`, phân trang; gán `visibility` + `source_id` theo bảng quy tắc của S5; tôn trọng `Retry-After`, hỗ trợ `--limit` (R10). | FR-012/AC-001, BR-005 | T-069, T-055, T-018 | `.../connectors/confluence.py`, `tests/test_connector_confluence.py` | Crawl fixture ra `SourceDocument` đủ metadata citation; `visibility` khớp bảng S5; watermark biên **inclusive (`>=`)** |
| T-071 | BE | Connector GitLab | Dùng `mcp_gitlab.client`; crawl repo file + MR/issue description theo cấu hình; **`MCP_GITLAB_PATH_DENY` deny-glob áp ở connector** (ADR-0015 A1) — document bị chặn ghi `ingest_failures{stage: redact, code: blocked_by_policy}`; `visibility` từ project visibility + member role (S5). | FR-012/AC-001, BR-005, R4 | T-069, T-055, T-023 | `.../connectors/gitlab.py`, `tests/test_connector_gitlab.py` | `.env`/`*.pem` **không** vào corpus và **có** một hàng `ingest_failures` tương ứng; `visibility` khớp S5 |
| T-072 | BE | Connector OpenSearch — **mặc định TẮT** | Dùng `mcp_opensearch.client`; chỉ bật qua allowlist `MCP_INGEST_OPENSEARCH_INDICES` (**mặc định rỗng ⇒ connector tắt**, ADR-0012 A5); `source_id` ổn định qua ILM rollover theo S5; **không dùng PIT** trong checkpoint (ADR-0012 A4). | FR-012/AC-001 | T-069, T-055, T-033 | `.../connectors/opensearch.py`, `tests/test_connector_opensearch.py` | Không set biến → `sources` báo connector **disabled** và `run --source opensearch` không crawl gì; có allowlist → chỉ crawl index trong allowlist |
| T-073 | BE | Stage `normalize` → `redact` | Normalize qua `mcp_common.content` rồi **redact + deny-glob ở tầng ingest, trước `chunk`** (ADR-0012 A1 / 0015 A1 — nếu chỉ redact ở tool layer thì secret bị persist vào `kb.chunks` rồi phát lại qua `kb_semantic_search`); document bị chặn → `ingest_failures{stage: redact, code: blocked_by_policy}`. **Nếu PO chọn corpus đồng nhất quyền: từ chối `visibility != 'team'` tại đây** với cùng cơ chế (ADR-0016 Phần 2). | FR-012/AC-001, FR-012/AC-002, R4, BR-003 | T-069, T-013; **Gate B: needs_user_decision #4** | `.../pipeline/{normalize,redact}.py`, `tests/test_stage_redact.py` | Secret trong nội dung nguồn **không** xuất hiện trong `kb.chunks` (test tích hợp truy vấn thật); document bị chặn nhìn thấy được trong `ingest_failures` |
| T-074 | BE | Stage `chunk` + `chunk_config_hash` | Chunker heading-aware có overlap, giữ `heading_path` cho citation chính xác, `token_count`; hash cấu hình chunker → `chunk_config_hash` (đổi size/overlap ⇒ **re-chunk** thay vì skip). | FR-012/AC-001, FR-012/AC-003, BR-005 | T-073 | `.../pipeline/chunk.py`, `tests/test_chunker.py` | `heading_path` đúng trên tài liệu nhiều cấp; đổi chunk size ⇒ `chunk_config_hash` đổi ⇒ document được re-chunk (test) |
| T-075 | BE | Stage `embed` + `persist` (upsert idempotent) | Embed theo lô qua port T-058; transaction: `UPSERT documents` (+ `last_seen_run_id`, `last_seen_at`) → `DELETE chunks` → `INSERT chunks`. **Hai khoá skip**: `content_hash` **và** `chunk_config_hash` giống ⇒ skip chunk+embed nhưng **LUÔN UPDATE metadata citation** (`title`, `source_uri`, `container`, `author`, `source_updated_at`, `last_seen_*` — ADR-0012 A4, nếu không thì page đổi tên/chuyển space giữ citation cũ và hỏng BR-005). `UNIQUE(source_type, source_id)` ⇒ re-ingest luôn là UPDATE. | FR-012/AC-001, FR-012/AC-003, BR-005 | T-074, T-058, T-057 | `.../pipeline/{embed,persist}.py`, `tests/test_persist_idempotent.py` | Chạy pipeline 2 lần trên cùng dữ liệu ⇒ **số document/chunk không đổi**; đổi tên page mà không đổi nội dung ⇒ `source_uri`/`title` **được cập nhật** dù chunk bị skip |
| T-076 | BE | Checkpoint + cách ly lỗi theo nguồn + `ingest_runs` | `pg_try_advisory_lock(hashtext('mcp-ingest:'||source_type))` trên **session riêng, không pooled**; **một hàng `ingest_runs` cho mỗi nguồn mỗi run** (ADR-0012 A3); nguồn lỗi ⇒ `status=partial`, `error_summary` += , **KHÔNG nâng checkpoint**, các nguồn khác vẫn chạy; `new_cursor = min(watermark của doc fail) − ε`, không có doc fail thì `max(watermark đã commit)`, biên inclusive `>=`; **bất kỳ doc fail ⇒ `partial`** (ADR-0012 A2, R18). | FR-012/AC-002 | T-075 | `.../pipeline/{run,checkpoint}.py`, `tests/test_checkpoint.py` | Test: nguồn B chết giữa run ⇒ nguồn A vẫn commit, `status=partial`, cursor của B **không nhảy**; doc fail ở giữa ⇒ cursor lùi về trước nó; hai process song song ⇒ process thứ hai không chạy trùng nguồn |
| T-077 | BE | `kb.ingest_failures` + `run --retry-failed` | Upsert `ingest_failures{attempts++, stage, code, last_error}` sau `MCP_INGEST_MAX_DOC_RETRIES` (mặc định 2); `run --retry-failed` lấy document **từ bảng này** thay vì `iter_documents(cursor)` — đường lấy lại nội dung fail vĩnh viễn mà checkpoint đã vượt qua (ADR-0012 A2/A5). | FR-012/AC-002 | T-076 | `.../pipeline/failures.py`, `.../cli.py`, `tests/test_retry_failed.py` | Doc fail 3 lần ⇒ có hàng `ingest_failures` với `attempts=3` + `stage` + `code`; `run --retry-failed` ingest lại đúng nó và **xoá hàng** khi thành công |
| T-078 | BE | Reconcile/tombstone + safety valve | `mode=full`: **chỉ khi `status=success` VÀ `documents_seen >= 0.8 ×` số document hiện có** thì trong **một** transaction: `UPDATE documents SET deleted_at=now() WHERE source_type=$1 AND last_seen_run_id IS DISTINCT FROM $run_id AND deleted_at IS NULL` **+ `DELETE FROM kb.chunks WHERE document_id IN (…)`** (xoá vật lý chunk: chunk của nội dung đã xoá chiếm slot ứng viên của ANN scan và làm index phình — ADR-0011 A2). Valve chặn ⇒ `IngestError{stage: reconcile}`, **không** tombstone (ADR-0012 A3, R18). | FR-012/AC-002, FR-011/AC-001 | T-076 | `.../pipeline/reconcile.py`, `tests/test_reconcile_valve.py` | Test: crawl chết ở 30% ⇒ **không** tombstone và có `IngestError{stage: reconcile}`; crawl đủ ⇒ document biến mất ở nguồn được tombstone và chunk **bị xoá vật lý** |
| T-079 | BE | `status` + `sources` (freshness NFR-004) | `status [--json]`: bảng theo nguồn `last_success_at`, staleness, số doc/chunk, số hàng `ingest_failures`; khớp `IngestStatusRow`/`IngestSourceConfigRow` của contract. | FR-012/AC-002, NFR-004 | T-076, T-068 | `.../commands/{status,sources}.py`, `tests/test_status.py` | `mcp-ingest status --json` validate theo contract; staleness tính đúng; **ngưỡng so sánh chờ PO (Open question 5)** — hiện chỉ báo số, không phán quyết |
| T-080 | BE | `reembed --model … [--source …]` | Re-embed theo lô khi đổi embedding model: cập nhật `chunks.embedding`, `embedding_model`, `embedded_at`; chạy được từng nguồn, resume được, không phá `documents`. | FR-012/AC-003, FR-011/AC-001 | T-075, T-058 | `.../commands/reembed.py`, `tests/test_reembed.py` | Đổi model ⇒ mọi chunk có `embedding_model` mới và `mcp-pgvector` khởi động lại được (startup check model khớp); ngắt giữa ⇒ chạy lại tiếp tục đúng chỗ |
| T-081 | BE | `prune [--tombstoned] [--older-than Nd]` | Xoá vật lý bia mộ `deleted_at` + áp retention — **không có lệnh này thì `kb` chỉ tăng** (ADR-0011 A5 / 0012 A5). `--dry-run` báo số hàng sẽ xoá. | FR-012/AC-003, NFR-004 | T-078 | `.../commands/prune.py`, `tests/test_prune.py` | `prune --tombstoned --older-than 30d` xoá đúng bia mộ đủ tuổi, không đụng document sống; **giá trị retention mặc định chờ PO** — cho tới lúc đó lệnh **bắt buộc** truyền `--older-than` |
| T-082 | BE | Scheduler + runbook vận hành pipeline | Mẫu `cron`/`launchd` (đề xuất SA: incremental mỗi giờ, full reconcile 03:00) + runbook: đọc exit code, xử lý `partial`, chạy `--retry-failed`, `maintenance_work_mem` riêng cho session build HNSW (R12), không bao giờ cấp `mcp_ingest_rw` cho MCP server nào. | FR-012/AC-002, NFR-004, R12 | T-079, T-081 | `infra/scheduler/{com.mcp.ingest.plist,crontab.example}`, `docs/runbook-ingest.md` | Job chạy được thật một lần và ghi `ingest_runs`; runbook có mục xử lý cho cả 3 exit code; **cadence cuối chờ PO xác nhận** |
| T-083 | BE | `mcp-ingest` — integration test end-to-end | Trên compose (Postgres+pgvector): crawl fixture → redact → chunk → embed (provider fake deterministic) → persist; rồi truy vấn lại **qua `mcp-pgvector`** để chứng minh citation resolve về item gốc; test nguồn unreachable; test re-ingest không nhân bản. | FR-012/AC-001, FR-012/AC-002, FR-012/AC-003, FR-011/AC-001, BR-005 | T-077, T-078, T-079, T-064 | `packages/mcp_ingest/tests/test_integration.py` | 3 AC của FR-012 có test tương ứng xanh; nội dung ingest tìm lại được bằng `kb_semantic_search` với citation là URL gốc; coverage ≥ 80% code đã đổi |
| T-084 | BE | Bộ câu hỏi eval Phase 3 (Journey 3) | Mở rộng `eval/questions.yaml` cho synthesis + queue state, gồm case **không có dữ liệu index liên quan** và case **bộ lọc loại hết** (phải phân biệt được hai câu trả lời); chạy tay qua prompt `semantic_synthesis`. | FR-013/AC-001, FR-013/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-003 | T-065, T-083, T-061 | `eval/questions.yaml`, `docs/claude-usage/journey-3.md` | ≥ 10 câu hỏi Phase 3; câu trả lời trích URL **gốc** phía sau embedding + ARN queue; case rỗng nói rõ "không tìm thấy dữ liệu đã index" |
| T-085 | BE | Sign-off Phase 3 + kiểm chứng cross-cutting toàn nền tảng | Snapshot **cả 49 tool** so với `api-contract.yaml`; `assert_readonly_tool_surface` trên 9 package + phủ method mà `mcp-ingest` gọi; test unknown write-tool ở tầng JSON-RPC cho cả 9 server; `doctor` 9 server; đăng ký Claude Desktop (NFR-005); `mcp-ingest status` so với bound freshness. | FR-014/AC-001, FR-014/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-001, NFR-002, NFR-004, NFR-005 | T-084, T-066, T-061, T-083, T-053 | `docs/signoff/phase-3.md`, `scripts/verify_tool_surface.py` | 49 tool khớp contract, 0 tool ghi trên cả 9 nguồn; 3 prompt + 6 lệnh CLI hiện diện; `mcp_ingest_rw` không xuất hiện trong env của bất kỳ MCP server nào |
| T-086 | BE | **Spike S3** — hybrid search (sau Phase 3) | Đánh giá có cần `tsvector` + RRF: đo recall của truy vấn từ khoá chính xác (tên hàm, mã lỗi) trên corpus thật so với vector-only; kết luận có/không thêm cột + migration `0007`. | FR-011/AC-001, NFR-003, R8 | T-085 | `docs/spikes/S3-hybrid-search.md` | Bảng so sánh recall vector-only vs hybrid trên ≥ 20 truy vấn từ khoá; kết luận có/không kèm ước lượng công; **không** implement trong plan này |

## Parallelism

Không có FE để đồng bộ. Song song ở đây là **giữa các họ task BE độc lập**, và điểm chốt là:
9 họ server chỉ chia sẻ `mcp_common` + `api-contract.yaml`, không gọi nhau, không chia sẻ
state — nên sau khi nền tảng xanh, chúng chạy song song không cần đàm phán.

| Có thể chạy song song | Điều kiện | Ghi chú |
|---|---|---|
| **T-001 (S1) ∥ T-002…T-004 ∥ T-032 (S4) ∥ T-054 (S2, phần cài/đo model)** | — | 4 việc "khảo sát/scaffold" không phụ thuộc nhau. S1 nên chạy **sớm nhất có thể** vì kết quả của nó quyết định integration test của cả Phase 1 và 2. S4 và phần đo model của S2 có thể chạy sớm hơn phase của chúng nếu muốn giảm rủi ro — chỉ **kết luận** mới cần đặt ở đầu phase. |
| **T-005…T-009 ∥ T-015 ∥ T-016** | T-002 xong | `config`/`logging`/`errors`/`envelope`/`render` là 5 module gần như độc lập (chỉ `envelope → errors`, `render → envelope`); compose không phụ thuộc code. |
| **T-010 ∥ T-011** | T-007 xong | `http` và `readonly` không phụ thuộc nhau. |
| **Họ Confluence (T-017…T-022) ∥ Họ GitLab (T-023…T-028)** | T-014 xong | Hai họ hoàn toàn độc lập. Trong mỗi họ, thứ tự `client → read_api/mappers → tools → tests` là **tuần tự bắt buộc**. |
| **5 họ Phase 2 song song: OpenSearch (T-033…T-037) ∥ Kibana (T-038…T-040) ∥ CloudWatch (T-041…T-043,T-045) ∥ Kafka (T-046…T-048) ∥ Redis (T-049…T-051)** | T-014 xong; Kafka thêm T-032 | Đây là cửa sổ song song lớn nhất của dự án (5 luồng). T-044 (prompt `incident_investigation`) là **điểm hợp lưu**: cần CloudWatch + OpenSearch + Kibana đã có tool. |
| **Họ SQS/SNS (T-059…T-061) ∥ toàn bộ nhánh DB/pgvector/ingest (T-056…T-058, T-062…T-083)** | T-014 xong | SQS/SNS **không** cần Postgres, không cần embedding, không cần S2/S5 ⇒ nên khởi động **ngay đầu Phase 3** song song với migration, để nhánh DB (đường dài nhất của Phase 3) không bị nối tiếp thêm. |
| **T-070 (Confluence connector) ∥ T-071 (GitLab connector) ∥ T-072 (OpenSearch connector)** | T-069 + T-055 xong | 3 connector độc lập; mỗi cái dùng `client.py` của package nguồn tương ứng (đã có từ Phase 1/2). |
| **T-079 ∥ T-080 ∥ T-081** | T-076/T-078 xong | `status`, `reembed`, `prune` là 3 lệnh CLI độc lập nhau. |
| **T-029/T-052/T-084 (eval) ∥ task code của phase sau** | Phase tương ứng xong | Eval là việc chạy tay + review, không chặn code. |

**Đường tuần tự dài nhất (critical path):**
`T-002 → T-007 → T-010 → T-012 → T-014 → [Phase 1 họ Confluence] → T-031 → T-032 → [Phase 2 họ dài nhất: CloudWatch] → T-053 → T-054 → T-056 → T-057 → T-058 → T-062 → T-063 → T-064 → (T-069 → T-070/71 → T-073 → T-074 → T-075 → T-076 → T-077/78) → T-083 → T-085`.
Rút ngắn được ở hai chỗ: (a) chạy S2/S4 sớm trước phase của chúng, (b) khởi động họ SQS/SNS
song song ngay từ đầu Phase 3.

## Definition of Done

Áp cho **mọi** task, không chỉ task cuối phase:

1. **Test trước, xanh sau:** unit test không cần network (`respx`/stubber/mock), integration test
   dùng `infra/docker-compose.yml`, integration với hệ thật gắn `@pytest.mark.live`.
2. **Coverage ≥ 80% trên code đã đổi**; `mcp_common` giữ mức cao hơn vì là điểm ảnh hưởng chung
   của 10 package (R11).
3. **Lint/type xanh:** `ruff check` + `mypy` + rule cấm `print()`.
4. **Contract conformance:** `tools.snapshot.json` của package khớp đúng các operation tương ứng
   trong `api-contract.yaml` (tên tool, inputSchema, outputSchema); mọi output validate được theo
   schema contract trên **cả 4 nhánh** `ok`/`empty`/`not_found`/`error`. Sai khác ⇒ sửa **code**,
   không sửa contract; cần sửa contract ⇒ **quay lại stage `sa`**.
5. **Read-only (NFR-001):** `tests/test_tools_readonly.py` của package xanh, gồm: tool surface
   snapshot, allowlist operation/command, transport assertion GET/HEAD, assertion contract
   `x-readonly: true` / `x-side-effects: none`, test gọi tên tool "ghi" không tồn tại (assert ở
   tầng **JSON-RPC**). Startup credential check là **điều kiện serve**, không phải cảnh báo.
6. **Citation (BR-002 / FR-015):** `citations` khác rỗng khi `status ∈ {ok, partial}`; mỗi item có
   `citation_ref`; `empty`/`not_found` là `status` thành công và có câu render tường minh.
7. **stdio an toàn (NFR-005):** không byte nào ra stdout ngoài JSON-RPC; log JSON chỉ ra stderr.
8. **Timeout (NFR-002):** mỗi nguồn có test endpoint không tới được → error code tường minh trong
   ≈21s (< deadline 25s), đúng 2 lần thử; nguồn dùng SDK đồng bộ thêm test executor cạn.
9. **Secret:** không secret thật trong repo; chỉ `.env.example`; redaction có test; nội dung
   nhạy cảm **không** persist vào `kb.chunks`.
10. **Phase sign-off:** `docs/signoff/phase-N.md` có checklist NFR-001/002/005 (+ NFR-004 ở Phase
    3) đã chạy, và `doctor` xanh hoặc lý do VPN đã ghi rõ để QA đưa vào regression-report.

## Risks & mitigations

| # | Rủi ro | Task xử lý | Giảm thiểu / ghi chú của Lead |
|---|---|---|---|
| R1 | VPN không tới được 5 nguồn remote | **T-001**, T-022, T-028, T-037, T-040, T-045 | S1 chạy **trước** Phase 1. Nếu unreachable: vẫn implement bằng `respx` + fixture payload thật; integration test bị hoãn và **phải nêu trong regression-report**, không được lặng lẽ skip. |
| R2 | Ô nhiễm stdout hỏng phiên stdio | T-002 (lint cấm `print`), **T-006** | Guard + test tự động là điều kiện của slice end-to-end đầu tiên, không để cuối. |
| R3 | Prompt injection gián tiếp qua nội dung nguồn | T-013, T-009 | `wrap_untrusted` + nhãn + giới hạn kích thước. **Không triệt tiêu được** — `docs/claude-usage/` phải khuyến nghị không bật auto-approve lệnh khi dùng các server này. |
| R4 | Secret bị **persist** vào `kb.chunks` rồi phát lại qua semantic search | T-013, **T-073**, T-071, T-050 | Redaction ở **hai** điểm áp: tool layer **và** stage `redact` của pipeline. T-073 có test tích hợp truy vấn thật để chứng minh secret không vào corpus. |
| R5 | Context bloat 49 tool | T-016, T-027, T-036, T-043 | ≤ 12 tool/server, description ≤ 3 câu, `MCP_MAX_OUTPUT_BYTES` 128 KiB; tài liệu khuyến nghị bật theo phase. |
| R6 | Chất lượng embedding chưa đo trên dữ liệu thật | **T-054** | Bake-off **trước khi embed toàn bộ**; hai model cùng 1024d ⇒ đổi không cần migrate schema, chỉ `reembed` (T-080). |
| R7 | `confluent-kafka` cài fail | **T-032**, T-046 | `ports.py` `KafkaReader` ⇒ đổi sang `kafka-python` không lan ra `tools.py`; T-046 có test chứng minh đổi adapter không sửa tool layer. |
| R8 | Không có hybrid search | **T-086** | Ở v1 giảm thiểu bằng việc Claude vẫn có `gitlab_search_code`/`opensearch_search_logs` cho tra cứu chính xác. |
| R9 | Pipeline chỉ chạy khi máy dev mở ⇒ dữ liệu cũ | T-064, T-079, T-082 | `kb_list_sources` trả `last_ingested_at` + `staleness_hours` để Claude **tuyên bố độ mới** thay vì im lặng. Ngưỡng chờ PO. |
| R10 | Crawl toàn bộ đụng rate limit | T-070, T-071, T-010 | Incremental theo watermark là mặc định; `Retry-After` được tôn trọng **và kẹp theo budget**; `--limit`. |
| R11 | `mcp_common` là điểm ảnh hưởng chung của 10 package | T-005…T-014 | Coverage cao + test riêng; **breaking change trong `mcp_common` phải chạy lại suite của cả 9 package** trước khi merge. |
| R12 | HNSW build tốn RAM | T-056, T-082 | `maintenance_work_mem` riêng cho session build; build index sau lô nạp đầu tiên. |
| R13 | `kb` là nguồn duy nhất dữ liệu ra khỏi hệ nguồn ⇒ `kb_semantic_search` trả nội dung hạn chế cho bất kỳ ai | **T-055**, **T-067**, **T-073** | Là **quyết định chặn của Gate B** (ADR-0016 Phần 2), không phải ghi chú. Hai nhánh đã được lên task tường minh: từ chối ingest `restricted` (T-073) **hoặc** RBAC per-user là must-have Phase 3 (T-067). |
| R14 | Layout `packages/` lệch mô tả `backend/` của CLAUDE.md ⇒ `squad-backend` có thể bị chặn quyền ghi | — (orchestrator) | **Chặn dispatch backend, không chặn plan này.** Plan này theo `packages/` đúng ADR-0001 + `architecture.md`. Cần orchestrator xác nhận ở Gate B rồi cập nhật CLAUDE.md hoặc phạm vi ghi của `squad-backend`. |
| R15 | Quy tắc suy ra `visibility` + `source_id` chưa được định nghĩa | **T-055** | S5 chạy **sau** khi PO trả lời Open question 3 và **trước** T-070/T-071/T-072. Nhãn là ảnh chụp lúc crawl ⇒ full reconcile để cập nhật, phải nói rõ khi bật RBAC. |
| R16 | Executor cạn làm treo mọi tool call sau đó, **vô hình** | **T-012**, T-045, T-048 | `max_workers=4` + queue có biên; queue đầy → `upstream_unavailable` ngay. Có test riêng cho CloudWatch và Kafka (hai nguồn SDK đồng bộ). |
| R17 | Kafka auto-create topic biến **chính test âm FR-007 AC-002** thành thao tác ghi | T-046, **T-048** | Chặn 3 chỗ (config, không truyền `topic=`, ACL deny `Create`) + test so sánh topic list trước/sau khi chạy suite. |
| R18 | Mất dữ liệu âm thầm ở pipeline (checkpoint vượt qua doc fail; reconcile tombstone hàng loạt) | **T-076**, **T-077**, **T-078** | `min(watermark doc fail) − ε`; bất kỳ doc fail ⇒ `partial`; `ingest_failures` + `--retry-failed`; safety valve 0.8. Ba task này là nơi FR-012 AC-002 thực sự được bảo đảm. |
| R19 *(mới — từ breakdown này)* | `mcp-pgvector` cần embed câu hỏi bằng **cùng** provider, nhưng ADR-0010 đặt port ở `mcp_ingest/ports.py` ⇒ server read-only phải import package chứa đường ghi `mcp_ingest_rw` | **T-058**, T-062 | Giữ đúng layout ADR-0010, nhưng `mcp_ingest.embedding` **không được import psycopg** và có **import-linter test** khẳng định điều đó; `mcp-pgvector` chỉ import `ports` + `embedding`. Nếu SA muốn tách hẳn thành package riêng thì đó là thay đổi layout ⇒ quay lại `sa`. Xem open question #1. |
| R20 *(mới — từ breakdown này)* | Ngưỡng NFR-002/003/004 + retention của `prune` chưa có ⇒ test verification có **assertion rỗng** | T-030, T-053, T-079, T-081, T-085 | Task vẫn tồn tại và vẫn chạy; đến khi PO chốt, test assert theo **bộ số ADR-0006 A2** và `prune` **bắt buộc** truyền `--older-than` (không có mặc định ngầm). Mỗi chỗ như vậy có comment `# THRESHOLD TBD (Open question 1/4/5)` để QA grep ra được. |

## Phụ thuộc vào quyết định của user (Gate B) — bản đồ tới task

Orchestrator dùng bảng này để chỉ cho user **đúng** những task bị ảnh hưởng. Không quyết định
nào trong số này chặn việc viết plan; 4/6 chặn **việc bắt đầu** một nhóm task cụ thể.

| # | Quyết định (từ `needs_user_decision` của SA) | Chặn gì | Task bị ảnh hưởng | Được giải quyết bởi |
|---|---|---|---|---|
| 1 | **Layout `packages/` vs `backend/`** (R14) | **CHẶN dispatch `squad-backend`** cho *toàn bộ* plan | Mọi task (T-001…T-086) | Orchestrator cập nhật CLAUDE.md / phạm vi ghi của `squad-backend`. Plan này theo `packages/`. |
| 2 | **Kafka client: `confluent-kafka` vs `kafka-python`** | Chặn họ Kafka Phase 2 | T-032 (spike quyết), T-046, T-047, T-048 | **Spike S4 = T-032** → đóng ADR-0009. Có thể để S4 tự quyết bằng kết quả cài đặt; user chỉ cần xác nhận. |
| 3 | **Embedding provider/model: `bge-m3` vs `multilingual-e5-large`** | Chặn embed thật của Phase 3 (không chặn schema vì cùng 1024d) | T-054 (spike quyết), T-056 (`dimensions`), T-058, T-075, T-080, T-063 | **Spike S2 = T-054** → đóng ADR-0010. |
| 4 | **Confluence Cloud vs Server/DC** | Chặn chi tiết auth/endpoint của họ Confluence | T-017 (biến settings), T-018 (allowlist endpoint + startup check), T-019, T-022, T-070 | Chỉ cần một câu trả lời của user; T-017/T-018 đã tách biến settings để đổi được nhưng **allowlist path khác nhau** giữa hai bản. |
| 5 | **RBAC/`visibility` có phải must-have của Phase 3 (ADR-0016 Phần 2)** | Chặn phạm vi pgvector query server + stage tagging của pipeline | **T-067 (chỉ tồn tại nếu "có")**, T-073 (từ chối `restricted` nếu "không"), T-055, T-063, T-071 | PO/user trả lời Open question 3 **trước khi code Phase 3**; S5 = T-055 chạy sau đó. |
| 6 | **Ngưỡng NFR-002/003/004 + retention mặc định của `prune`** | Không chặn task nào; chặn **assertion cuối** của task verification | T-030, T-053, T-079, T-081, T-085 | PO chốt số. Đến lúc đó dùng bộ số ADR-0006 A2 + bắt buộc `--older-than` (R20). |

## Coverage matrix — AC → task

| FR / AC | Task phủ |
|---|---|
| FR-001/AC-001 | T-019, T-020, T-022 |
| FR-001/AC-002 | T-020, T-022, T-030 |
| FR-001/AC-003 | T-018, T-022 |
| FR-002/AC-001 | T-024, T-025, T-026, T-027, T-028 |
| FR-002/AC-002 | T-025, T-026, T-027, T-028, T-030 |
| FR-002/AC-003 | T-023, T-028 |
| FR-003/AC-001 | T-021, T-029 |
| FR-003/AC-002 | T-009, T-021, T-029 |
| FR-004/AC-001 | T-034, T-035, T-036, T-037 |
| FR-004/AC-002 | T-033, T-035, T-036, T-037 |
| FR-005/AC-001 | T-039, T-040 |
| FR-005/AC-002 | T-039, T-040 |
| FR-006/AC-001 | T-042, T-043, T-045 |
| FR-006/AC-002 | T-042, T-043, T-045 |
| FR-007/AC-001 | T-047, T-048 |
| FR-007/AC-002 | T-047, T-048 |
| FR-007/AC-003 | T-046, T-048 |
| FR-008/AC-001 | T-050, T-051 |
| FR-008/AC-002 | T-050, T-051 |
| FR-008/AC-003 | T-049, T-051 |
| FR-009/AC-001 | T-044, T-052 |
| FR-009/AC-002 | T-044, T-052 |
| FR-010/AC-001 | T-060, T-061 |
| FR-010/AC-002 | T-060, T-061 |
| FR-010/AC-003 | T-059, T-061 |
| FR-011/AC-001 | T-063, T-064, T-066, T-078, T-080, T-083, T-086 |
| FR-011/AC-002 | T-063, T-066 |
| FR-011/AC-003 | T-056, T-062, T-066 |
| FR-012/AC-001 | T-056, T-058, T-069, T-070, T-071, T-072, T-073, T-074, T-075, T-083 |
| FR-012/AC-002 | T-057, T-068, T-073, T-076, T-077, T-078, T-079, T-082, T-083 |
| FR-012/AC-003 | T-057, T-074, T-075, T-080, T-081, T-083 |
| FR-013/AC-001 | T-065, T-084 |
| FR-013/AC-002 | T-065, T-084 |
| FR-014/AC-001 | T-010, T-011, T-012, T-014, T-018, T-023, T-033, T-036, T-038, T-041, T-046, T-049, T-059, T-062, T-069, T-085 |
| FR-014/AC-002 | T-007, T-011, T-012, T-014, T-022, T-028, T-037, T-040, T-045, T-048, T-051, T-061, T-066, T-085 |
| FR-015/AC-001 | T-008, T-009, T-013, T-016, T-019, T-024, T-029, T-039, T-047, T-050, T-052, T-060, T-064, T-084, T-085 |
| FR-015/AC-002 | T-008, T-009, T-016, T-029, T-052, T-063, T-084, T-085 |

**37 AC id / 37 được phủ. `uncovered_ac` rỗng.**

Ghi chú đếm: `requirements.md` HANDOFF ghi `AC=36`, nhưng liệt kê thực tế trong file là **37**
AC id (FR-001:3, FR-002:3, FR-003:2, FR-004:2, FR-005:2, FR-006:2, FR-007:3, FR-008:3,
FR-009:2, FR-010:3, FR-011:3, FR-012:3, FR-013:2, FR-014:2, FR-015:2 = 37). Plan này phủ cả 37;
lệch 1 ở con số tổng của BA là lỗi đếm trong HANDOFF, không phải AC thiếu.

| NFR | Task phủ |
|---|---|
| NFR-001 | T-003, T-011, T-014, T-022, T-028, T-031, T-037, T-040, T-045, T-048, T-051, T-053, T-061, T-066, T-085 |
| NFR-002 | T-001, T-004, T-010, T-012, T-030, T-045, T-053, T-085 |
| NFR-003 | T-029, T-052, T-066, T-084, T-086 |
| NFR-004 | T-064, T-079, T-081, T-082, T-085 |
| NFR-005 | T-005, T-006, T-012, T-016, T-031, T-053, T-085 |

---

HANDOFF
status: done
artifacts: [docs/squad/mcp-data-platform/implementation-plan.md]
counts: BE=86 FE=0 other=0
tasks: setup=16 (T-001…T-016, gồm spike S1) phase1=15 (T-017…T-031) phase2=22 (T-032…T-053, gồm spike S4) phase3=33 (T-054…T-086, gồm spike S2/S5/S3)
spikes: S1=T-001 (trước Phase 1) S4=T-032 (đầu Phase 2) S2=T-054 (đầu Phase 3) S5=T-055 (đầu Phase 3, sau khi PO trả lời Open question 3) S3=T-086 (sau Phase 3)
uncovered_ac: []
first_e2e_slice: T-002 → T-005…T-014 → T-017 → T-018 → T-019 → T-020 (chỉ confluence_search_pages) → T-022 → T-031
needs_user_decision:
  1. Layout `packages/` vs `backend/` trong CLAUDE.md (R14) — **BLOCKING cho dispatch squad-backend, ảnh hưởng toàn bộ T-001…T-086**. Plan này theo `packages/` đúng ADR-0001 + architecture.md; cần orchestrator cập nhật CLAUDE.md hoặc phạm vi ghi của squad-backend trước khi dispatch.
  2. Kafka client (confluent-kafka vs kafka-python) — ảnh hưởng T-032, T-046, T-047, T-048; **S4 = T-032 quyết bằng kết quả cài đặt thật**, user chỉ cần xác nhận.
  3. Embedding provider/model (bge-m3 vs multilingual-e5-large) — ảnh hưởng T-054, T-056, T-058, T-063, T-075, T-080; **S2 = T-054 quyết**; cùng 1024d nên không đổi schema.
  4. Confluence Cloud vs Server/DC — ảnh hưởng T-017, T-018, T-019, T-022, T-070 (allowlist endpoint + startup check khác nhau giữa hai bản); cần một câu trả lời của user, spike không giải quyết được.
  5. RBAC/visibility có phải must-have Phase 3 (ADR-0016 Phần 2, Open question 3) — ảnh hưởng T-055, T-063, T-067 (**task này chỉ tồn tại nếu câu trả lời là "corpus được chứa restricted"**), T-071, T-073. Cần trả lời **trước khi code Phase 3**.
  6. Ngưỡng NFR-002/003/004 + retention mặc định của `mcp-ingest prune` — ảnh hưởng assertion cuối của T-030, T-053, T-079, T-081, T-085; không chặn việc task tồn tại.
  (Lead không thêm quyết định mới nào cần user; mục #1 vẫn là mục duy nhất chặn dispatch.)
open_questions:
  - **Q-L1 (SA, kỹ thuật — không chặn):** ADR-0010 đặt port `EmbeddingProvider` ở `mcp_ingest/ports.py`, nhưng `mcp-pgvector` (read-only, role `mcp_query_ro`) phải embed câu hỏi bằng **cùng** provider ⇒ server read-only import package chứa đường ghi `mcp_ingest_rw` (R19). Plan giả định: giữ đúng layout ADR-0010 + `mcp_ingest.embedding` **không import psycopg** + import-linter test khẳng định (T-058). Nếu SA muốn package riêng thì đó là thay đổi layout ⇒ quay lại stage `sa`.
  - **Q-L2 (BA/PO, đếm — không chặn):** `requirements.md` HANDOFF ghi `AC=36` nhưng file liệt kê **37** AC id. Plan phủ cả 37. Cần BA sửa con số trong HANDOFF để QA không đếm lệch.
  - **Q-L3 (SA/QA — không chặn):** `api-contract.yaml` ghi FR-014 AC-002 nằm ở **tầng JSON-RPC** (unknown tool → JSON-RPC error, **không** phải `ErrorEnvelope`). Plan đã đặt helper assert đúng tầng đó (T-014) và dùng lại ở 9 họ server; QA cần assert cùng tầng, không assert `ErrorEnvelope`.
  - **Q-L4 (vận hành — không chặn):** báo cáo spike ghi vào `docs/spikes/`, sign-off vào `docs/signoff/`, eval vào `eval/` — **ngoài** `docs/squad/<feature>/` (thư mục đó thuộc role tài liệu). Cần orchestrator xác nhận `squad-backend` được phép ghi 3 thư mục này cùng với `packages/`, `infra/`, `scripts/`, `ci/`.
</content>
</invoke>
