# MCP Data Platform — Implementation Plan

> Nguồn đầu vào: `requirements.md` (FR-001…FR-015, 37 AC id, NFR-001…NFR-005),
> `architecture.md` (đã reconcile với 16 ADR, 29 amendment sau design review 2026-10-01),
> `api-contract.yaml` (49 operation tool — **bề mặt mặc định 48 tool**, 49 khi bật
> `MCP_OPENSEARCH_ALLOW_DSL` (ADR-0008 A7) — + 6 operation CLI, `x-readonly: true` toàn bộ), `state.json`.
>
> **Cập nhật sau SA reconcile contract_issue (loops.spec=1, 2026-10-01)** — chỉ sửa tối thiểu,
> không đánh số lại task: T-015, T-016, T-036, T-039, T-051, T-053, T-054, T-055, T-056, T-067,
> T-068, T-073, T-085 + các bảng rủi ro / quyết định / HANDOFF tương ứng. Nguồn: `architecture.md`
> "Design review → Reconcile contract_issue từ squad-backend", ADR-0008 A5–A7, ADR-0010 A1–A2,
> ADR-0011 A6, ADR-0016 A1–A3.
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
   `inputSchema`/`outputSchema` của cả 49 operation tool (bề mặt mặc định 48, 49 với flag DSL). Mỗi task tool **không thiết kế schema**, nó chỉ
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
| T-015 | BE | Hạ tầng dev/test bằng Docker Compose | `pgvector/pgvector:pg16`, `redis:7` + **file ACL mẫu tạo user `mcp_ro` có `+acl|getuser`** (ADR-0008 A1) **và `+select`** (ADR-0008 A5 — để `db` 1..15 của contract dùng được; `+select` không phải quyền ghi), `apache/kafka` (KRaft single node), `localstack` (`sqs,sns`). 5 nguồn còn lại không emulate → fixture payload thật + `respx`. Tài liệu dev-setup đặt ở **`infra/dev-setup.md`** (chốt ở SA reconcile #13). | NFR-001, NFR-002 | T-002 | `infra/docker-compose.yml`, `infra/redis/users.acl`, `infra/localstack/init.sh`, `infra/dev-setup.md` | `docker compose up` lên đủ 4 service; `redis-cli ACL GETUSER mcp_ro` chạy được bằng chính user đó và liệt kê `+select`; `redis-cli --user mcp_ro -n 1 PING` thành công; LocalStack có 1 queue + 1 topic mẫu |
| T-016 | BE | `mcp-common config-emit` + tài liệu dùng trong Claude | Subcommand in đoạn JSON dán vào `claude_desktop_config.json` cho từng server (phục vụ verification NFR-005) + `docs/claude-usage/` (bật server theo phase, ngân sách context 48 tool mặc định / 49 khi bật flag DSL, R5) + snippet CLAUDE.md về kỷ luật citation (ADR-0014). | NFR-005, FR-015/AC-001, FR-015/AC-002 | T-005 | `.../mcp_common/cli.py`, `docs/claude-usage/README.md` | `uv run mcp-common config-emit --server confluence` in ra JSON hợp lệ dán được; tài liệu nêu rõ khuyến nghị không bật cả 9 server cùng lúc |

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
| T-036 | BE | OpenSearch tool nhóm B | `opensearch_count`, `opensearch_aggregate`, `opensearch_search_dsl` (validate body theo allowlist cấu trúc; dùng `MCP_TOOL_DEADLINE_OPENSEARCH_SEARCH_DSL` cho query chậm hợp lệ; **chỉ đăng ký khi `MCP_OPENSEARCH_ALLOW_DSL=true`**, mặc định tắt; `size` kẹp về `limit` + warning, `from` ≤ 900, `from+size` ≤ 1000; `aggs` nhận nhưng không trả kết quả, thêm `meta.warnings` trỏ sang `opensearch_aggregate` — ADR-0008 A7) + `tools.snapshot.json`. | FR-004/AC-001, FR-004/AC-002, FR-014/AC-001 | T-035 | `.../tools.py`, `.../tools.snapshot.json` | Snapshot khớp contract: **5 tool mặc định, 6 khi bật flag**; `search_dsl` với `script` bị chặn; `timeout_s` validate theo deadline riêng |
| T-037 | BE | `mcp-opensearch` — bộ test đầy đủ | readonly + contract + unit (respx/mock client) + integration `@pytest.mark.live`; test riêng cho `scroll`/PIT/`script` bị chặn. | FR-004/AC-001, FR-004/AC-002, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-036, T-014 | `packages/mcp_opensearch/tests/*` | Test xanh; coverage ≥ 80% code đã đổi |
| T-038 | BE | `mcp-kibana` skeleton + `client.py` + startup check | Thin REST client cho 3 endpoint GET (`_find`, get saved object, và space/status cho startup check); header `kbn-xsrf` không bao giờ dùng cho method ghi. | FR-005, FR-014/AC-001, NFR-002 | T-012, T-010, T-011 | `packages/mcp_kibana/{pyproject.toml,src/mcp_kibana/{settings,client,server,cli}.py}` | Chỉ 3 endpoint GET trong allowlist; startup check xanh/đỏ đúng; timeout theo budget |
| T-039 | BE | Kibana — 3 tool + deep link đúng khung thời gian | `kibana_find_saved_objects`, `kibana_get_saved_object`, `kibana_build_dashboard_link` (URL `_g=(time:(from,to))` dựng **thuần hàm** từ input, nhưng tool thực hiện **đúng một** `GET /api/saved_objects/dashboard/{id}` để xác minh dashboard tồn tại và lấy `title` — chống citation tới dashboard id bịa, FR-015; giữ theo contract, SA reconcile #4a) + mappers + snapshot. | FR-005/AC-001, FR-005/AC-002, FR-015/AC-001 | T-038, T-008, T-009 | `.../{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | Link sinh ra mở đúng dashboard + đúng time range; test respx assert `build_dashboard_link` gọi **đúng 1** request GET; dashboard id không tồn tại → `not_found`; **`kibana_find_saved_objects` không khớp → `empty`** (không phải `not_found`, theo quy ước `ResultStatus`, SA reconcile #4b); 3 tool khớp contract |
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
| T-051 | BE | `mcp-redis` — bộ test đầy đủ | readonly + contract + unit + integration trên `redis:7` của compose với user `mcp_ro`; test âm cho `SET`/`DEL`/`EXPIRE`; **test integration với `db=1`** (ACL có `+select`, ADR-0008 A5) cho `redis_scan_keys`/`redis_get_key`/`redis_key_info`; test khẳng định tool không bao giờ tự gửi `SELECT`/`SWAPDB`/`MOVE` và startup check không coi `+select` là quyền ghi. | FR-008/AC-001, FR-008/AC-002, FR-008/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-001 | T-050, T-014, T-015 | `packages/mcp_redis/tests/*` | Test xanh; mọi lệnh ghi bị từ chối ở cả code và ACL server; call với `db=1` trả `ok`/`empty`, không `forbidden`; coverage ≥ 80% code đã đổi |
| T-052 | BE | Bộ câu hỏi eval Phase 2 (Journey 2) | Mở rộng `eval/questions.yaml` cho incident investigation, gồm case **một nguồn rỗng trong khung giờ** và case nghi Kafka lag/Redis cache; chạy tay qua prompt `incident_investigation`. | FR-009/AC-001, FR-009/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-003 | T-044, T-037, T-040, T-045, T-048, T-051 | `eval/questions.yaml`, `docs/claude-usage/journey-2.md` | ≥ 10 câu hỏi Phase 2; câu trả lời có log/metric excerpt + dashboard link, mỗi mệnh đề một citation; case rỗng nêu gap thành một dòng |
| T-053 | BE | Sign-off Phase 2 | `doctor` 5 server; đăng ký Claude Desktop (NFR-005); suite read-only toàn bộ (NFR-001); kiểm chứng NFR-002 + test executor cạn cho 5 server (trong đó CloudWatch/Kafka là SDK đồng bộ). | NFR-001, NFR-002, NFR-005, FR-014/AC-001, FR-014/AC-002 | T-052, T-030 | `docs/signoff/phase-2.md`, `packages/mcp_{opensearch,kibana,cloudwatch,kafka,redis}/tests/test_timeout_budget.py` | Checklist đủ: 24 tool Phase 2 mặc định (5+3+7+5+4; 25 khi bật flag DSL) hiện trong Claude, tổng 39 tool sau Phase 1+2 (40 với flag), 0 tool ghi, timeout ≈21s < 25s ở mọi nguồn |

### Phase 3 — SQS/SNS, pgvector query server, ingest/embedding pipeline (FR-010…FR-013)

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Done when |
|----|----|----|----|----|----|----|----|
| T-054 | BE | **Spike S2** — embedding bake-off | Bake-off `bge-m3` vs `multilingual-e5-large` (cùng 1024d ⇒ không cần migrate schema) trên bộ câu hỏi NFR-003 **trước khi embed toàn bộ**; đo recall@k, latency CPU, RAM, thời gian tải model. **Đầu ra: chốt provider/model, đóng ADR-0010.** **Trạng thái (ADR-0010 A1):** harness + test đã xong, **chưa đo** — egress HF bị chặn (403); `BAAI/bge-m3` (1024d, cosine, normalize) là mặc định **provisional** để T-056/T-058/T-075 chạy được; ADR-0010 vẫn `proposed`. Phần còn lại của task: chạy đo thật khi gỡ chặn HF hoặc nạp model offline (`HF_HUB_OFFLINE=1`). | FR-011, FR-012, R6; đóng needs_user_decision #3 | T-029 (bộ câu hỏi), T-002 | `docs/spikes/S2-embedding-bakeoff.md`, `scripts/bakeoff_embedding.py` | Bảng so sánh 2 model **bằng số đo thật** + model được chọn + `model_id`/`dimensions` chốt; ADR-0010 được SA đóng. Không chặn T-056/T-058 (dùng provisional, cùng 1024d ⇒ đổi model chỉ cần `reembed`) |
| T-055 | BE | **Spike S5** — quy tắc `visibility` + `source_id` per connector | Định nghĩa quy tắc suy ra `documents.visibility` (`team`/`restricted`) cho Confluence (space permission), GitLab (project visibility + member role), OpenSearch; và quy tắc dựng `source_id` **ổn định qua ILM rollover** (ADR-0012 A5). **Chạy sau khi PO trả lời Open question 3.** Đầu ra đóng ADR-0016 (R15). | FR-012/AC-001, FR-012/AC-003, BR-003, BR-005; đóng needs_user_decision #5 | T-001; Gate B: quyết định RBAC (ADR-0016 Phần 2) — **đã quyết team-only (ADR-0016 A1)**; quy tắc default-deny đã chốt ở A2 | `docs/spikes/S5-visibility-source-id.md` | Bảng quy tắc per-connector, có case "quyền đổi ở nguồn sau khi crawl"; `source_id` có test ví dụ cho index rollover; ADR-0016 được SA đóng |
| T-056 | BE | Migration 0001–0005 + `mcp-ingest db upgrade` | `packages/mcp_ingest` skeleton (Typer) + runner migration SQL đánh số theo dõi bằng `kb.schema_migrations`; `0001_extensions.sql` (vector, pgcrypto), `0002_schema_kb.sql` (`documents`, `chunks` + unique `(source_type, source_id)` và `(document_id, chunk_index)`), `0003_indexes.sql` (HNSW `vector_cosine_ops` m=16 ef_construction=64 + btree), `0004_ingest_state.sql`, `0005_roles.sql` (`mcp_ingest_rw`, `mcp_query_ro` với `default_transaction_read_only=on`). **`db upgrade` kết nối bằng `MCP_INGEST_ADMIN_DSN`** (role có `CREATE` + `CREATEROLE`, superuser ở dev), fallback `MCP_INGEST_PGVECTOR_DSN` chỉ khi role đó đã được cấp DDL; thiếu cả hai ⇒ lỗi cấu hình tường minh; biến này **chỉ** dùng cho `db upgrade` (ADR-0011 A6) và có mặt trong `.env.example`. | FR-012/AC-001, FR-011/AC-003 | T-054 (`dimensions` = 1024 theo model provisional `bge-m3`, ADR-0010 A1), T-015 | `packages/mcp_ingest/{pyproject.toml,src/mcp_ingest/{__init__,settings,cli,db}.py,migrations/0001…0005.sql}`, `.env.example` | `mcp-ingest db upgrade` áp được từ DB rỗng lên 0005 bằng `MCP_INGEST_ADMIN_DSN` và **idempotent** khi chạy lại; chỉ có `mcp_ingest_rw` DSN ⇒ lỗi tường minh (không lỗi CREATE EXTENSION nửa chừng); `mcp_query_ro` không INSERT được (test khẳng định) |
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
| T-067 | BE | **ĐÓNG — không áp dụng** ~~Filter `visibility` + RBAC cho Phase 3~~ | User đã chọn **corpus team-only** (ADR-0016 A1, 2026-10-01) ⇒ không có RBAC per-user ở Phase 3. Task giữ id để truy vết, **không dispatch**; phần việc thay thế nằm ở T-073 (từ chối `visibility != 'team'` + purge khi đổi nhãn). Cột `visibility` vẫn giữ làm đường mở sang RBAC khi chuyển remote (BR-004). | BR-003, ADR-0016 A1 (không phủ AC nào riêng) | — | — | Đã đóng: lý do "user chọn corpus team-only (ADR-0016 A1)"; T-073 thay thế |
| T-068 | BE | `mcp-ingest` — CLI shell + report schema + exit code | Typer app với 6 lệnh theo contract (`db upgrade`, `run`, `status`, `sources`, `reembed`, `prune`); model `IngestRunReport`/`IngestSourceResult`/`IngestError`/`MigrationResult`/`IngestStatusRow`/`IngestSourceConfigRow` khớp `components.schemas`; **exit code 0 success / 1 partial / 2 failed**; log JSON ra stderr. Chọn DSN theo lệnh: `db upgrade` → `MCP_INGEST_ADMIN_DSN` (fallback `MCP_INGEST_PGVECTOR_DSN`); mọi lệnh khác → `MCP_INGEST_PGVECTOR_DSN` (ADR-0011 A6). | FR-012/AC-001, FR-012/AC-002 | T-056, T-006 | `packages/mcp_ingest/src/mcp_ingest/cli.py`, `.../reports.py`, `tests/test_cli_contract.py` | 6 lệnh `--help` chạy; output `--json` validate được theo 6 operation `x-interface: cli` của contract; exit code có test cho cả 3 giá trị; test: `run`/`reembed`/`prune`/`status`/`sources` **không bao giờ** đọc `MCP_INGEST_ADMIN_DSN` |
| T-069 | BE | Khung `SourceConnector` + registry + lệnh `sources` | Protocol `iter_documents(cursor, mode) -> SourceDocument{source_id, source_uri, raw_content, source_updated_at, visibility, …}`; registry connector; lệnh `sources` liệt kê connector + trạng thái cấu hình. **Connector dùng `client.py` của package nguồn, không bao giờ `read_api.py`** (ADR-0012 A4). | FR-012/AC-001, FR-014/AC-001 | T-068, T-014 | `.../connectors/{__init__,base,registry}.py`, `tests/test_connector_isolation.py` | `mcp-ingest sources` in đủ connector đã đăng ký; **test: không module connector nào import `read_api`**; `assert_readonly_tool_surface` phủ method mà connector gọi |
| T-070 | BE | Connector Confluence | Dùng `mcp_confluence.client`, crawl incremental theo watermark `lastModified`, phân trang; gán `visibility` + `source_id` theo bảng quy tắc của S5; tôn trọng `Retry-After`, hỗ trợ `--limit` (R10). | FR-012/AC-001, BR-005 | T-069, T-055, T-018 | `.../connectors/confluence.py`, `tests/test_connector_confluence.py` | Crawl fixture ra `SourceDocument` đủ metadata citation; `visibility` khớp bảng S5; watermark biên **inclusive (`>=`)** |
| T-071 | BE | Connector GitLab | Dùng `mcp_gitlab.client`; crawl repo file + MR/issue description theo cấu hình; **`MCP_GITLAB_PATH_DENY` deny-glob áp ở connector** (ADR-0015 A1) — document bị chặn ghi `ingest_failures{stage: redact, code: blocked_by_policy}`; `visibility` từ project visibility + member role (S5). | FR-012/AC-001, BR-005, R4 | T-069, T-055, T-023 | `.../connectors/gitlab.py`, `tests/test_connector_gitlab.py` | `.env`/`*.pem` **không** vào corpus và **có** một hàng `ingest_failures` tương ứng; `visibility` khớp S5 |
| T-072 | BE | Connector OpenSearch — **mặc định TẮT** | Dùng `mcp_opensearch.client`; chỉ bật qua allowlist `MCP_INGEST_OPENSEARCH_INDICES` (**mặc định rỗng ⇒ connector tắt**, ADR-0012 A5); `source_id` ổn định qua ILM rollover theo S5; **không dùng PIT** trong checkpoint (ADR-0012 A4). | FR-012/AC-001 | T-069, T-055, T-033 | `.../connectors/opensearch.py`, `tests/test_connector_opensearch.py` | Không set biến → `sources` báo connector **disabled** và `run --source opensearch` không crawl gì; có allowlist → chỉ crawl index trong allowlist |
| T-073 | BE | Stage `normalize` → `redact` | Normalize qua `mcp_common.content` rồi **redact + deny-glob ở tầng ingest, trước `chunk`** (ADR-0012 A1 / 0015 A1 — nếu chỉ redact ở tool layer thì secret bị persist vào `kb.chunks` rồi phát lại qua `kb_semantic_search`); document bị chặn → `ingest_failures{stage: redact, code: blocked_by_policy}`. **Corpus team-only (ADR-0016 A1): từ chối mọi document `visibility != 'team'` tại đây** với cùng cơ chế. **Đổi nhãn `team → restricted`** (hoặc space/project bị gỡ khỏi allowlist) với document đã có trong `kb`: nhãn được tính lại mọi run kể cả khi hash-skip ⇒ **xoá chunk + tombstone trong cùng transaction** và ghi `ingest_failures{redact, blocked_by_policy}` — không được chỉ "bỏ qua lần này" (ADR-0016 A2; phối hợp với persist của T-075). | FR-012/AC-001, FR-012/AC-002, R4, BR-003 | T-069, T-013 | `.../pipeline/{normalize,redact}.py`, `tests/test_stage_redact.py` | Secret trong nội dung nguồn **không** xuất hiện trong `kb.chunks` (test tích hợp truy vấn thật); document bị chặn nhìn thấy được trong `ingest_failures`; test: document `restricted` không vào `kb.chunks`; test: document đã ingest với `team` rồi relabel `restricted` (nội dung không đổi) ⇒ chunk bị xoá + `deleted_at` được đặt + có hàng `ingest_failures`; sau run, `SELECT count(*) FROM kb.documents WHERE visibility <> 'team' AND deleted_at IS NULL` = 0 |
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
| T-085 | BE | Sign-off Phase 3 + kiểm chứng cross-cutting toàn nền tảng | Snapshot **48 tool mặc định (49 khi bật `MCP_OPENSEARCH_ALLOW_DSL`)** so với `api-contract.yaml`; `assert_readonly_tool_surface` trên 9 package + phủ method mà `mcp-ingest` gọi; test unknown write-tool ở tầng JSON-RPC cho cả 9 server; `doctor` 9 server; đăng ký Claude Desktop (NFR-005); `mcp-ingest status` so với bound freshness. | FR-014/AC-001, FR-014/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-001, NFR-002, NFR-004, NFR-005 | T-084, T-066, T-061, T-083, T-053 | `docs/signoff/phase-3.md`, `scripts/verify_tool_surface.py` | 48 tool mặc định (49 với flag DSL) khớp contract, 0 tool ghi trên cả 9 nguồn; 3 prompt + 6 lệnh CLI hiện diện; `mcp_ingest_rw` không xuất hiện trong env của bất kỳ MCP server nào |
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
| R5 | Context bloat 48 tool (49 với flag DSL) | T-016, T-027, T-036, T-043 | ≤ 12 tool/server, description ≤ 3 câu, `MCP_MAX_OUTPUT_BYTES` 128 KiB; tài liệu khuyến nghị bật theo phase. |
| R6 | Chất lượng embedding chưa đo trên dữ liệu thật | **T-054** | Bake-off **trước khi embed toàn bộ**; hai model cùng 1024d ⇒ đổi không cần migrate schema, chỉ `reembed` (T-080). **Hiện trạng:** `bge-m3` provisional, chưa đo (HF bị chặn, ADR-0010 A1) — rủi ro còn mở. |
| R7 | `confluent-kafka` cài fail | **T-032**, T-046 | `ports.py` `KafkaReader` ⇒ đổi sang `kafka-python` không lan ra `tools.py`; T-046 có test chứng minh đổi adapter không sửa tool layer. |
| R8 | Không có hybrid search | **T-086** | Ở v1 giảm thiểu bằng việc Claude vẫn có `gitlab_search_code`/`opensearch_search_logs` cho tra cứu chính xác. |
| R9 | Pipeline chỉ chạy khi máy dev mở ⇒ dữ liệu cũ | T-064, T-079, T-082 | `kb_list_sources` trả `last_ingested_at` + `staleness_hours` để Claude **tuyên bố độ mới** thay vì im lặng. Ngưỡng chờ PO. |
| R10 | Crawl toàn bộ đụng rate limit | T-070, T-071, T-010 | Incremental theo watermark là mặc định; `Retry-After` được tôn trọng **và kẹp theo budget**; `--limit`. |
| R11 | `mcp_common` là điểm ảnh hưởng chung của 10 package | T-005…T-014 | Coverage cao + test riêng; **breaking change trong `mcp_common` phải chạy lại suite của cả 9 package** trước khi merge. |
| R12 | HNSW build tốn RAM | T-056, T-082 | `maintenance_work_mem` riêng cho session build; build index sau lô nạp đầu tiên. |
| R13 | `kb` là nguồn duy nhất dữ liệu ra khỏi hệ nguồn ⇒ `kb_semantic_search` trả nội dung hạn chế cho bất kỳ ai | **T-055**, **T-073** (T-067 đã đóng) | **Đã quyết team-only** (ADR-0016 A1): T-073 từ chối ingest `visibility != 'team'` và purge chunk khi relabel `team → restricted`; quy tắc default-deny S5 (A2). **Tồn dư** (A3): page Confluence bị đặt restriction mà không đổi nội dung còn tìm được tới full reconcile kế tiếp — PO xác nhận chu kỳ reconcile (T-082). |
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
| 3 | **Embedding provider/model: `bge-m3` vs `multilingual-e5-large`** | Chặn embed thật của Phase 3 (không chặn schema vì cùng 1024d) | T-054 (spike quyết), T-056 (`dimensions`), T-058, T-075, T-080, T-063 | **Spike S2 = T-054** → đóng ADR-0010. *Hiện trạng: provisional `bge-m3`, đo còn chờ (HF bị chặn); không chặn code.* |
| 4 | **Confluence Cloud vs Server/DC** | Chặn chi tiết auth/endpoint của họ Confluence | T-017 (biến settings), T-018 (allowlist endpoint + startup check), T-019, T-022, T-070 | Chỉ cần một câu trả lời của user; T-017/T-018 đã tách biến settings để đổi được nhưng **allowlist path khác nhau** giữa hai bản. |
| 5 | **RBAC/`visibility` có phải must-have của Phase 3 (ADR-0016 Phần 2)** | Chặn phạm vi pgvector query server + stage tagging của pipeline | T-067 (**đã đóng**), T-073 (từ chối `restricted` + purge khi relabel), T-055, T-063, T-071 | **Đã quyết 2026-10-01: team-only** (ADR-0016 A1, accepted). |
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
counts: BE=86 FE=0 other=0 (T-067 đóng — không áp dụng, giữ id; 85 task còn hiệu lực)
changed_tasks (SA reconcile 2026-10-01): [T-015, T-016, T-036, T-039, T-051, T-053, T-054, T-055, T-056, T-067, T-068, T-073, T-085]
tool_surface: 48 mặc định (49 khi MCP_OPENSEARCH_ALLOW_DSL=true); Phase 2 = 24 (25)
tasks: setup=16 (T-001…T-016, gồm spike S1) phase1=15 (T-017…T-031) phase2=22 (T-032…T-053, gồm spike S4) phase3=33 (T-054…T-086, gồm spike S2/S5/S3)
spikes: S1=T-001 (trước Phase 1) S4=T-032 (đầu Phase 2) S2=T-054 (đầu Phase 3) S5=T-055 (đầu Phase 3, sau khi PO trả lời Open question 3) S3=T-086 (sau Phase 3)
uncovered_ac: []
first_e2e_slice: T-002 → T-005…T-014 → T-017 → T-018 → T-019 → T-020 (chỉ confluence_search_pages) → T-022 → T-031
needs_user_decision:
  1. Layout `packages/` vs `backend/` trong CLAUDE.md (R14) — **BLOCKING cho dispatch squad-backend, ảnh hưởng toàn bộ T-001…T-086**. Plan này theo `packages/` đúng ADR-0001 + architecture.md; cần orchestrator cập nhật CLAUDE.md hoặc phạm vi ghi của squad-backend trước khi dispatch.
  2. Kafka client (confluent-kafka vs kafka-python) — ảnh hưởng T-032, T-046, T-047, T-048; **S4 = T-032 quyết bằng kết quả cài đặt thật**, user chỉ cần xác nhận.
  3. Embedding provider/model (bge-m3 vs multilingual-e5-large) — ảnh hưởng T-054, T-056, T-058, T-063, T-075, T-080; **S2 = T-054 quyết**; cùng 1024d nên không đổi schema. *(Hiện trạng: `bge-m3` provisional, đo còn chờ vì HF bị chặn — ADR-0010 A1.)*
  4. Confluence Cloud vs Server/DC — ảnh hưởng T-017, T-018, T-019, T-022, T-070 (allowlist endpoint + startup check khác nhau giữa hai bản); cần một câu trả lời của user, spike không giải quyết được.
  5. ~~RBAC/visibility có phải must-have Phase 3~~ — **ĐÃ QUYẾT: team-only** (ADR-0016 A1). T-067 đóng; T-073 từ chối `visibility != 'team'` + purge khi relabel. Follow-up không chặn: PO xác nhận cửa sổ tồn dư A3 (chu kỳ full reconcile) + allowlist space/project.
  6. Ngưỡng NFR-002/003/004 + retention mặc định của `mcp-ingest prune` — ảnh hưởng assertion cuối của T-030, T-053, T-079, T-081, T-085; không chặn việc task tồn tại.
  (Lead không thêm quyết định mới nào cần user; mục #1 vẫn là mục duy nhất chặn dispatch.)
open_questions:
  - **Q-L1 (SA, kỹ thuật — không chặn):** ADR-0010 đặt port `EmbeddingProvider` ở `mcp_ingest/ports.py`, nhưng `mcp-pgvector` (read-only, role `mcp_query_ro`) phải embed câu hỏi bằng **cùng** provider ⇒ server read-only import package chứa đường ghi `mcp_ingest_rw` (R19). Plan giả định: giữ đúng layout ADR-0010 + `mcp_ingest.embedding` **không import psycopg** + import-linter test khẳng định (T-058). Nếu SA muốn package riêng thì đó là thay đổi layout ⇒ quay lại stage `sa`.
  - **Q-L2 (BA/PO, đếm — không chặn):** `requirements.md` HANDOFF ghi `AC=36` nhưng file liệt kê **37** AC id. Plan phủ cả 37. Cần BA sửa con số trong HANDOFF để QA không đếm lệch.
  - **Q-L3 (SA/QA — không chặn):** `api-contract.yaml` ghi FR-014 AC-002 nằm ở **tầng JSON-RPC** (unknown tool → JSON-RPC error, **không** phải `ErrorEnvelope`). Plan đã đặt helper assert đúng tầng đó (T-014) và dùng lại ở 9 họ server; QA cần assert cùng tầng, không assert `ErrorEnvelope`.
  - **Q-L4 (vận hành — không chặn):** báo cáo spike ghi vào `docs/spikes/`, sign-off vào `docs/signoff/`, eval vào `eval/` — **ngoài** `docs/squad/<feature>/` (thư mục đó thuộc role tài liệu). Cần orchestrator xác nhận `squad-backend` được phép ghi 3 thư mục này cùng với `packages/`, `infra/`, `scripts/`, `ci/`.
</content>
</invoke>


---

# CHG-001 Option C + B4 Grounding — Company Knowledge tier (mở rộng, task T-087+)

> **Nguồn đầu vào CHG-001:** `requirements.md` (FR-016…FR-022, 26 AC id mới, GT-1..GT-7 ánh xạ vào
> FR-021; NFR-006…NFR-012), `architecture.md` ("Company Knowledge tier" + "Epic map E1..E8" + hai choke
> point), `api-contract.yaml` (13 tool mới: 8 Knowledge + 5 Jira, envelope `GroundedResultBase` tương
> thích ngược + `status=insufficient_evidence`), `records/decisions.md` (D-004 B4 in-scope; DK2/DK3),
> `records/backlog.md` (CHG-002 B2/B5…B10/ORCH **KHÔNG** làm — ngoài scope), ADR-0017…0022.
>
> **Nền hiện tại (T-001…T-086) ĐÃ XONG + go-live — KHÔNG lam lai.** Phần này **chỉ thêm** task mới,
> đánh số tiếp từ **T-087**. Mọi task vẫn `Owner = BE` (không FE), backend-only. Giữ **nguyên** mọi
> bất biến đã go-live: read-only tuyệt đối (0 write tool trên 13 tool mới), stdio (NFR-005/
> gateway in-process không cổng mạng), `vendors=none` + **không egress** (reranker/embedding local
> offline `HF_HUB_OFFLINE=1`), permission server-side **trước** grounding gate, mọi tool có test đi kèm.
>
> **Hai choke point (không nhầm lẫn):** #1 `enforce_permission()` default-deny **TRƯỚC** context
> assembly (E6); #2 context-pack assembler = grounding gate **SAU** permission (E8). Mỗi cái là **một**
> choke point cấu trúc riêng — không nhân bản nhau (L-001, ADR-0018 §2).

## Summary & sequencing strategy — CHG-001

Chiến lược: **đi theo đúng thứ tự epic E1 → E8 của `architecture.md`**, contract-first như nền cũ,
riskiest-assumption-first.

1. **E1 là choke point chặn trước mọi epic khác, và task đầu tiên của E1 là fix migration-locking
   (DK2 / R-006/R-007).** Mọi schema mới của CHG-001 chạy trên pgvector **đã có dữ liệu prod** (nền
   9-nguồn đã go-live). D-002 ĐK2 + D-003 DK2 buộc sửa migration-locking (`ADD CONSTRAINT … NOT VALID`
   + `VALIDATE` ở câu riêng; `CREATE INDEX CONCURRENTLY` **ngoài** transaction per-file; `lock_timeout`
   + `statement_timeout` ngắn; `ADD COLUMN` default hằng) **trước** lần đổi schema kế tiếp. Vì vậy
   **T-087 (fix migration-locking runner) là task đầu tiên tuyệt đối** và chặn T-088 (migration
   0007/0007b/0008), và cả hai chặn mọi epic còn lại.
2. **Walking skeleton của CHG-001** = một slice E1→E4→E8 mỏng: schema `document_permissions` + một
   claim đi qua `enforce_permission()` → grounding gate → trả `GroundedResult` với verdict
   `UNKNOWN`/`FACT`. Nó khoá sớm bốn rủi ro đắt nhất: migration-locking an toàn (R-006/R-007), một
   choke point duy nhất (L-001), envelope grounding tương thích ngược (EB-002), và no-evidence⇒UNKNOWN
   (BR-007).
3. **Grounding gate (E8) phải nằm SAU permission (E6) trong đường đi.** D-004 §3 cho phép **fallback
   tạm** cắm gate vào ranh giới `mcp_pgvector` **chỉ khi** B4 đến trước module context-pack assembler
   của E3 — plan này **không** dùng fallback đó: E3 (assembler) được xây trước E8, nên gate cắm thẳng
   vào assembler ngay từ đầu, một choke point duy nhất, không phải dời. (Nếu lịch buộc đảo, điều kiện
   D-004 §3a/§3b/§3c áp dụng — ghi ở Risks R-C7.)
4. **Ngưỡng confidence τ_fact/τ_low = TBD (L-002/NFR-010), KHÔNG bịa số.** Task eval (T-105) ghi rõ
   bộ số khởi tạo `τ_fact=0.6, τ_low=0.3` **chỉ dùng cho infrastructure test**, envelope mang
   `calibration_status: uncalibrated`, và τ chỉ chốt **khi gỡ egress HF** chạy golden-set thật. Các
   bất biến FACT/UNKNOWN/CONFLICT (GT-1..GT-4) **độc lập ngưỡng** nên test được **ngay**.
5. **13 tool mới nằm trên 2 server mới** (`mcp-knowledge` 8 tool, `mcp-jira` 5 tool) + 1 boundary
   in-process (`mcp_gateway`). Tổng bề mặt sau CHG-001 = **61 tool mặc định** (48 nền + 13 mới; 62 khi
   bật `MCP_OPENSEARCH_ALLOW_DSL`). Tài liệu `docs/claude-usage/` phải cảnh báo context budget mới.

## Patterns to mirror — CHG-001

**Brownfield giờ đây có code sản phẩm** — mirror các pattern của nền 9-nguồn (không còn greenfield):

| Pattern đã có (nền) | File tham chiếu | CHG-001 dùng ở |
|---|---|---|
| Cấu trúc server chuẩn `client.py`/`read_api.py`/`tools.py`/`mappers.py`/`tools.snapshot.json` | `packages/mcp_<source>/` (T-017…) | `mcp-jira` (T-091), `mcp-knowledge` (T-097) |
| 5-lớp read-only defense (allowlist transport + startup credential check + `@readonly_tool` + contract `x-readonly` + unknown-write-tool JSON-RPC) | `mcp_common.{http,readonly,runtime,testing}` (T-010…T-014) | mọi tool mới (T-091, T-097…T-100) |
| Envelope + citation ADR-0004 (`ToolResult`/`Citation`/`Meta`, `citations≠∅` khi `ok/partial`) | `mcp_common.envelope` (T-008) | `GroundedResultBase` mở rộng (T-096) |
| Migration runner đánh số bằng `kb.schema_migrations` | `mcp_ingest/db.py` + `migrations/000x.sql` (T-056) | runner migration-locking (T-087), 0007/0007b/0008 (T-088) |
| Connector dùng `client.py` **không** `read_api.py` + incremental watermark + reconcile/tombstone + `ingest_failures` | `mcp_ingest/connectors/*` + `pipeline/*` (T-069…T-078) | Jira connector (T-093) |
| Startup check role read-only (từ chối serve nếu ghi được) | `mcp_pgvector` (T-062), `mcp_gitlab` (T-023) | `mcp-knowledge`/`mcp-jira` startup check (T-091, T-097) |

**Lệnh bắt buộc trước khi code bất kỳ task E4/E6/E8:** chạy `ecc:code-architect` một lần trên
`architecture.md` + `api-contract.yaml` mục CHG-001 để lấy blueprint "files to create/modify + build
sequence" bám đúng layout `packages/` hiện có (nền đã có code để mirror — đây không còn greenfield).

## Setup tasks — CHG-001

| ID | Owner | Title | Mô tả | Covers | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-087 | BE | **DK2 — fix migration runner locking** (R-006/R-007, **chặn trước mọi epic CHG-001**) | Sửa runner `mcp_ingest/db.py` để chạy an toàn trên dữ liệu đã go-live: (a) tách file `*.concurrently.sql` ra **autocommit, ngoài `BEGIN`** (`CREATE INDEX CONCURRENTLY`), phát hiện index `INVALID` → `DROP`+retry; (b) hỗ trợ `ADD CONSTRAINT … NOT VALID` + `VALIDATE CONSTRAINT` ở câu **riêng**; (c) đặt `lock_timeout` + `statement_timeout` ngắn mỗi migration; (d) từ chối `ALTER COLUMN TYPE` tại chỗ. **Không** đổi 0001–0006 (empty-table deploy đầu đã an toàn). | NFR-012 (safety nền), DK2 (D-002 ĐK2 / D-003) | — (task đầu tiên tuyệt đối) | `packages/mcp_ingest/src/mcp_ingest/db.py`, `tests/test_migration_locking.py` | 1–3 | Test: file `*.concurrently.sql` chạy ngoài transaction (assert không có `BEGIN` bao quanh); `NOT VALID` + `VALIDATE` tách hai câu; `lock_timeout` set; index invalid → runner `DROP`+retry; chạy lại idempotent |
| T-088 | BE | **E1 — migration 0007/0007b/0008** (4 domain + source-authority) trên runner mới | `0007_knowledge_domains.sql`: `document_versions`, `entities`, `relationships`, `knowledge_summaries`, `document_permissions` (FK/CHECK `NOT VALID` → `VALIDATE` riêng; `ADD COLUMN` default hằng); `0007b_knowledge_indexes.concurrently.sql`: GIN `tsvector` trên `kb.chunks` + btree `relationships(src_entity_id)`/`(dst_entity_id)` + `document_versions(document_id,version)` + `document_permissions(document_id)` (**CONCURRENTLY ngoài txn**); `0008_source_authority.sql`: bảng + seed config source-authority theo loại fact + `confidence.weights (0.5/0.3/0.2)` + `freshness_horizon` (**không hardcode vào prompt**). | FR-016, FR-018, FR-019, FR-020, FR-021 (schema nền) | T-087 | `packages/mcp_ingest/migrations/{0007_knowledge_domains.sql,0007b_knowledge_indexes.concurrently.sql,0008_source_authority.sql}` | 2–4 | `db upgrade` từ schema 0006 (có dữ liệu) lên 0008 **không khoá full-table**, idempotent; schema khớp **đúng** ER của `architecture.md`; GIN tsvector + 4 domain hiện diện; `mcp_query_ro` SELECT được 4 domain, INSERT bị từ chối (test) |
| T-089 | BE | **E1 — DK3 Redis ACL + env-promotion CHG-001** | Ghi vào `squad-env-promotion` CHG-001 rằng Redis dev ACL (`mcp_ro` broad grants + `default nopass +@all`, R-013) **tuyệt đối không tái dùng** cho staging/shared; thêm bước env-promotion "Redis ACL least-privilege per-env" + checklist. Thêm `MCP_KNOWLEDGE_*`/`MCP_JIRA_*`/`MCP_GATEWAY_*` + `HF_HUB_OFFLINE=1` + `MCP_RERANKER_MODEL_PATH` + source-authority config path vào `.env.example`. | DK3 (D-002 ĐK3 / D-003), NFR-011 | T-088 | `.env.example`, `docs/env-promotion-chg001.md` (hoặc mục trong env-promotion) | 0.5–1 | `.env.example` có đủ biến mới + `HF_HUB_OFFLINE=1`, không secret thật; env-promotion ghi rõ Redis ACL không tái dùng + checklist least-privilege per-env |
| T-090 | BE | **E1 — `mcp-ingest db upgrade` tích hợp 0007+ + verify DK2 trên populated DB** | Chạy 0007/0007b/0008 qua runner mới trên bản copy có dữ liệu (dev), đo thời gian khoá, xác nhận không block read-path; cập nhật `db status` liệt kê 0007/0007b/0008; backup-check trước migration (như cab-pack §5). | FR-016 (nền schema), NFR-012 | T-088, T-089 | `packages/mcp_ingest/src/mcp_ingest/cli.py`, `tests/test_db_upgrade_chg001.py` | 1–2 | `db upgrade` trên DB có N document áp 0007→0008 không drop read; `db status --json` liệt kê 3 migration mới; test lock-time dưới `lock_timeout` |

## Tasks — CHG-001 (theo epic E1..E8)

### E2 — Jira source #10 (Live Jira MCP + connector), dep E1

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-091 | BE | `mcp-jira` skeleton + `client.py` (Cloud/Server split) + startup read-only check | Package `packages/mcp_jira/` theo layout chuẩn; `settings.py` (`MCP_JIRA_*`, biến chọn **Cloud `/rest/api/3`** vs **Server/DC `/rest/api/2`** + Agile `/rest/agile/1.0`); `client.py` dùng `mcp_common.http`, allowlist **chỉ GET** (`/search`, `/issue/{key}`, `/project(/search)`, `/sprint/{id}`, `/board/{id}/sprint`, + current-user cho startup check); JQL **bounded** (không ghi); con trỏ opaque (Cloud `nextPageToken` / Server `startAt`) đóng trong `client.py`; startup check `GET /myself` + assert token **không** tạo/transition được. | FR-017/AC-003 (BR-006/EB-001, ADR-0019, ADR-0003); FR-017/AC-001 (nền transport) | T-088 | `packages/mcp_jira/{pyproject.toml,src/mcp_jira/{settings,client,server,cli}.py}` | 2–4 | `uv run mcp-jira --help` chạy; request ngoài allowlist / method ghi → `not_permitted`; startup check fail → không serve; cursor opaque đổi được giữa Cloud/Server không sửa tool layer |
| T-092 | BE | Live Jira — 5 tool + `read_api.py` + mappers + snapshot | `jira_search_issues` (JQL bounded, `limit≤100`), `jira_get_issue` (key `^[A-Z][A-Z0-9]+-[0-9]+$`), `jira_list_projects`, `jira_get_sprint`, `jira_list_board_sprints`; map → `JiraIssue*`/`JiraProject*`/`JiraSprint*`; `Citation` = Jira URL/issue-key/project-key/sprint-id; `empty` (list không khớp) vs `not_found` (key/id không tồn tại) theo contract. | FR-017/AC-001, FR-017/AC-002 (ADR-0019; tool `jira_search_issues`/`jira_get_issue`/`jira_list_projects`/`jira_get_sprint`/`jira_list_board_sprints`) | T-091, T-008, T-009 | `packages/mcp_jira/src/mcp_jira/{read_api,mappers,tools}.py`, `.../tools.snapshot.json` | 2–4 | 5 tool trong `tools/list`; snapshot khớp 5 operation `/mcp/jira/...` của contract; issue không tồn tại → `not_found`; list rỗng → `empty` với `citations: []`; citation là Jira URL mở được |
| T-093 | BE | Jira connector (ingest `source_type='jira'`) — incremental + full-reconcile | Connector dùng `mcp_jira.client` (**không** `read_api.py`, ADR-0012 A4); incremental `updated >=` watermark (biên inclusive `>=`); full-reconcile tombstone với safety-valve 0.8 **tái dùng** `pipeline/reconcile.py` của nền; `visibility` default-deny theo S5-style rule; redact + deny-glob ở stage `redact` (dùng chung T-073). | FR-017/AC-004 (ADR-0019/0012/0016; BR-005); FR-012 (nền pipeline mở rộng) | T-092, T-069, T-073, T-076, T-078 | `packages/mcp_ingest/src/mcp_ingest/connectors/jira.py`, `tests/test_connector_jira.py` | 2–4 | Crawl fixture Jira ra `SourceDocument` đủ metadata citation; incremental `updated>=` watermark inclusive; issue biến mất ở nguồn + safety-valve đạt ⇒ document tombstone (`deleted_at`) + chunk xoá vật lý, không nhân bản khi re-ingest; connector không import `read_api` (test) |
| T-094 | BE | `mcp-jira` + Jira connector — bộ test đầy đủ + test âm ghi | `test_tools_readonly.py` (surface + transport GET/HEAD + contract `x-readonly` + unknown write-tool ở tầng **JSON-RPC**), `test_contract.py` (snapshot + 4 nhánh), unit respx theo fixture Cloud **và** Server/DC, `test_integration.py @pytest.mark.live`; **test âm: create issue / transition / add comment đều không tồn tại / bị từ chối** (BR-006). | FR-017/AC-003, FR-014/AC-001, FR-014/AC-002, NFR-006 (ADR-0019/0003) | T-093, T-014 | `packages/mcp_jira/tests/*`, `tests/fixtures/jira/*.json` | 1–3 | Test xanh (live skip nếu Jira unreachable, ghi regression-report); 0 tool ghi; không đường code nào create/transition/comment; coverage ≥ 80% code đã đổi |

### E3 — Hybrid-RAG + reranker local offline + context-compression + context-pack assembler, dep E1

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-095 | BE | E3 — Hybrid retrieval: tsvector + pgvector + metadata + RRF (k=60) | Module `mcp_knowledge/retrieval/hybrid.py`: keyword leg (`tsvector @@`, config `simple` cho code / `english`+`vietnamese`-aware cho văn bản) ∪ vector leg (pgvector `<=>`, tái dùng HNSW nền) ∪ metadata filter; fuse bằng **RRF k=60 deterministic** (không cần score so sánh được); `deleted_at IS NULL` bắt buộc; role `mcp_query_ro` (`BEGIN READ ONLY`). | FR-016/AC-001 (ADR-0020; tool `search_company_knowledge`/`search_code`); FR-016/AC-004 (EB-004/EB-005, `mcp_query_ro`) | T-088 | `packages/mcp_knowledge/src/mcp_knowledge/retrieval/{hybrid,rrf}.py`, `tests/test_rrf.py`, `tests/test_hybrid.py` | 3–5 | RRF k=60 cho ra ranking deterministic (test cố định input→output); keyword leg khớp định danh/mã lỗi chính xác; chỉ dùng `mcp_query_ro`, 0 write; `deleted_at IS NULL` áp mọi truy vấn (test) |
| T-096 | BE | E3 — `GroundedResultBase` envelope (mở rộng ADR-0004 tương thích ngược) | Mở rộng `mcp_common.envelope`: `GroundedResultBase` **thêm** `claims[]` (mỗi claim: `verdict`, 6 field provenance `source/source_version/owner/updated_time/confidence/evidence{document_id,chunk_id}`, `positions[]` cho CONFLICT) + `grounding_summary` (đếm verdict, `reranker`, `calibration_status`); **thêm** `status=insufficient_evidence`; **giữ nguyên** bất biến nền (`citations≠∅` khi `ok/partial`, `empty`/`not_found` là status). Base 9-nguồn **không** đổi. | FR-021 (ADR-0018/0004; EB-002); FR-016/AC-001 (`grounding_summary` đếm verdict) | T-008 | `packages/mcp_common/src/mcp_common/envelope.py`, `tests/test_grounded_envelope.py` | 2–3 | Test: `GroundedResult` validate theo contract `GroundedResultBase`; envelope nền 9-nguồn **không** regress (test cũ T-008 vẫn xanh); `insufficient_evidence` là status hợp lệ; claim `FACT` thiếu bất kỳ 6 field → schema reject |
| T-097 | BE | E3 — reranker local offline `bge-reranker-v2-m3` + RRF-only fallback cờ | `mcp_knowledge/rerank/local.py`: cross-encoder load từ **đĩa local, `HF_HUB_OFFLINE=1`**, lazy một lần/process; nếu weights thiếu → **RRF-only fallback** sau một cờ tường minh + báo `grounding_summary.reranker=disabled` (**không** âm thầm hạ chất lượng, L-002). Assertion: không socket ra ngoài khi rerank. | FR-016/AC-003 (fallback transparency, L-002, ADR-0020); FR-016/AC-004 (`HF_HUB_OFFLINE=1`, no egress); NFR-011 | T-095 | `packages/mcp_knowledge/src/mcp_knowledge/rerank/local.py`, `tests/test_rerank_offline.py`, `tests/test_no_egress_rerank.py` | 2–4 | Test: weights thiếu → fallback RRF-only + `reranker=disabled` (không raise, không giả vờ full-quality); `HF_HUB_OFFLINE=1` được assert; không outbound socket trong lúc rerank (test); model load lazy một lần |
| T-098 | BE | E3 — context-compression (giữ provenance) + context-pack assembler skeleton | `mcp_knowledge/pack/compress.py` (extractive/rule-based, **không** LLM, không egress) **không bao giờ nén mất provenance** của claim (GT-7); `mcp_knowledge/pack/assembler.py` = khung context-pack assembler (sẽ thành grounding gate ở T-104) — ở E3 chỉ gom claim + provenance, chưa đóng verdict. | FR-021/AC-006 (GT-7 provenance preserved, ADR-0018 §6); FR-016/AC-001 (context-pack) | T-096, T-097 | `packages/mcp_knowledge/src/mcp_knowledge/pack/{compress,assembler}.py`, `tests/test_compress_provenance.py` | 2–4 | Test GT-7: nén rồi mỗi claim **vẫn** giữ đủ 6 field provenance (không claim nào mất `evidence`/`updated_time`); compression deterministic, 0 egress; assembler gom claim + provenance (chưa verdict) |

### E4 — Knowledge MCP 8 tool + envelope grounding, dep E1,E2,E3

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-099 | BE | E4 — `mcp-knowledge` skeleton + `client.py` + startup read-only check | Package `packages/mcp_knowledge/` layout chuẩn; `settings.py` (`MCP_KNOWLEDGE_*`, DSN `mcp_query_ro`, reranker path, source-authority config path); `client.py` dùng psycopg3 role `mcp_query_ro` **mọi txn `BEGIN READ ONLY`** + Live Jira qua `mcp_jira.client`; startup check `SHOW transaction_read_only=on` + `has_table_privilege('kb.chunks','INSERT')=false` + model embedding khớp dữ liệu + reranker path tồn tại (hoặc fallback cờ). | FR-016/AC-004 (EB-004, `mcp_query_ro`, ADR-0010/0003); NFR-006 (startup read-only) | T-095, T-091, T-088 | `packages/mcp_knowledge/{pyproject.toml,src/mcp_knowledge/{settings,client,server,cli}.py}` | 2–4 | Dán DSN `mcp_ingest_rw` vào `MCP_KNOWLEDGE_DSN` → **từ chối serve**; model lệch dữ liệu → từ chối serve; `uv run mcp-knowledge serve` lên stdio |
| T-100 | BE | E4 — tool nhóm entity/version: `get_service`, `get_repository`, `get_knowledge_summary`, `get_document_version` | Đọc `kb.entities`/`kb.knowledge_summaries`/`kb.document_versions` read-only; `get_document_version` trả version `current`/`superseded` + `source_version`/`author`/`source_updated_at` + citation; `not_found` khi entity/document không tồn tại hoặc tombstone; `empty` khi entity tồn tại nhưng không summary/version. | FR-020/AC-001, FR-020/AC-002 (ADR-0022; tool `get_service`/`get_repository`/`get_knowledge_summary`/`get_document_version`) | T-099, T-096 | `packages/mcp_knowledge/src/mcp_knowledge/tools/entity.py` | 2–4 | 4 tool khớp operation contract; document tombstone → `not_found`; entity không summary → `empty` không lỗi; version có đủ `source_version`/`author`/`source_updated_at` + citation |
| T-101 | BE | E4 — `find_related_knowledge` (recursive CTE bounded ≤3 hop + cycle-detect + LIMIT fanout) | `WITH RECURSIVE` trên `kb.entities`/`kb.relationships`, **depth ≤ 3**, cycle-detection (visited-set), `LIMIT` fanout mỗi hop; mỗi cạnh trả `rel_type`, `depth`, citation; `not_found` (entity gốc) vs `empty` (không cạnh); filter `rel_types`. | FR-020/AC-001, FR-020/AC-003 (bound/terminate, ADR-0022; tool `find_related_knowledge`) | T-100 | `packages/mcp_knowledge/src/mcp_knowledge/tools/related.py`, `tests/test_cte_bounded.py` | 2–4 | Test FR-020/AC-003: đồ thị có chu trình + fan-out cao ⇒ traversal **dừng** ở depth≤3, cycle-detect, LIMIT; mỗi cạnh có `rel_type`+`depth`+citation; entity không tồn tại → `not_found` |
| T-102 | BE | E4 — `search_company_knowledge` + `search_code` + `get_jira_context` (nối E3 pipeline, chưa gate) + prompt | 3 tool business gọi đường E3 (`enforce_permission` placeholder → hybrid → rerank → compress → assembler); `search_code` giới hạn `source_type='gitlab'` tsvector `simple`; `get_jira_context` hợp nhất Jira live + snapshot (chuẩn bị CONFLICT ở E5); trả `GroundedResult`; prompt `company_knowledge_lookup` bắt buộc trình bày UNKNOWN khi `insufficient_evidence`. **Verdict đóng ở E8 (T-104), không ở đây.** | FR-016/AC-001, FR-016/AC-002 (tool `search_company_knowledge`/`search_code`/`get_jira_context`, ADR-0020) | T-098, T-100, T-092 | `packages/mcp_knowledge/src/mcp_knowledge/tools/search.py`, `.../prompts.py`, `.../tools.snapshot.json` | 3–5 | 8 tool trong `tools/list`; snapshot khớp 8 operation `/mcp/knowledge/...`; `search_company_knowledge` không khớp → `empty`/`insufficient_evidence` không bịa claim; prompt chứa chỉ thị trình bày UNKNOWN |
| T-103 | BE | E4 — `mcp-knowledge` bộ test tool-surface + contract + read-only | `test_tools_readonly.py` (8 tool, transport, contract `x-readonly`, unknown write-tool JSON-RPC), `test_contract.py` (snapshot 8 tool + envelope `GroundedResultBase` 4+1 nhánh gồm `insufficient_evidence`), unit theo fixture. | FR-016/AC-004, FR-014/AC-001, FR-014/AC-002, NFR-006 (ADR-0003/0004) | T-102, T-014, T-096 | `packages/mcp_knowledge/tests/{test_tools_readonly,test_contract}.py` | 1–3 | 8 tool read-only, 0 ghi; snapshot khớp contract; `insufficient_evidence` validate; coverage ≥ 80% code đã đổi |

### E6 — Permission server-side ENFORCE (choke point #1, default-deny) + adversarial test, dep E1,E4

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-104 | BE | E6 — `enforce_permission()` choke point #1 (default-deny, TRƯỚC context assembly) + adversarial test | `mcp_knowledge/permission/enforce.py`: lọc ứng viên theo `kb.document_permissions`/`visibility` **default-deny** (vắng grant = deny), chạy **trước** hybrid retrieval ranking; **một** choke point mọi đường đi qua (thay placeholder của T-102); structural test khẳng định không đường vòng + permission chạy **trước** grounding gate. **Adversarial test FR-019 (bắt buộc):** nạp đúng query khớp nội dung một document `restricted` không-grant ⇒ document **không** là candidate, **không** trong context-pack, **không** citation. | FR-019/AC-001, FR-019/AC-002 (adversarial, L-001), FR-019/AC-003 (structural single choke point); NFR-007 (ADR-0016/0021) | T-102, T-088 | `packages/mcp_knowledge/src/mcp_knowledge/permission/enforce.py`, `tests/test_permission_adversarial.py`, `tests/test_permission_single_chokepoint.py` | 3–5 | **Adversarial:** restricted-không-grant query → document không xuất hiện ở candidate/pack/citation (test đỏ nếu rò); **structural:** mọi đường tới context assembly đi qua đúng **một** `enforce_permission()`, chạy **trước** grounding gate (test); default-deny: vắng grant = loại |

### E5 — Live-vs-Knowledge + freshness + conflict + source-authority, dep E2,E4

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-105 | BE | E5 — Live-vs-Knowledge decision + freshness factor + source-authority config | `mcp_knowledge/liveness/reconcile.py`: khi cùng claim có cả snapshot (knowledge) + live (Jira) → so giá trị; khác ⇒ chuẩn bị `CONFLICT` + `authority_note` theo `0008_source_authority` (theo **loại fact**, configurable, không hardcode); freshness factor theo `freshness_horizon` (fact quá hạn bị **hạ** confidence, **không** thành sai); `meta.data_freshness`/staleness như `kb_list_sources`. | FR-018/AC-001, FR-018/AC-002, FR-018/AC-003 (ADR-0018 §4, spec §42; tool `search_company_knowledge`/`get_jira_context`) | T-104, T-092 | `packages/mcp_knowledge/src/mcp_knowledge/liveness/reconcile.py`, `tests/test_live_vs_knowledge.py` | 2–4 | Snapshot=live ⇒ một claim + provenance hai phía + `data_freshness` ở `meta`; khác ⇒ chuẩn bị `CONFLICT` hai `positions` + `authority_note` từ config (không hardcode); fact quá `freshness_horizon` ⇒ freshness factor giảm, claim không bị làm sai, staleness quan sát được ở `meta` |

### E8 — B4 grounding gate (choke point #2, SAU permission) + confidence deterministic + GT-1..GT-7 + eval/telemetry, dep E3,E4,E6

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-106 | BE | E8 — grounding gate (context-pack assembler = choke point #2) + confidence deterministic + verdict | Biến assembler (T-098) thành **grounding gate duy nhất** (choke point #2), chạy **SAU** `enforce_permission` (T-104): gán mỗi claim đúng **một** verdict `FACT`/`LOW_CONFIDENCE`/`UNKNOWN`/`CONFLICT`; confidence **deterministic** `retrieval×agreement×freshness` (hình học, weights `0.5/0.3/0.2` từ `0008`); claim không evidence hợp lệ ⇒ `UNKNOWN` + message cố định "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này."; confidence **không** promote claim không-nguồn thành FACT; ≥2 nguồn khác giá trị ⇒ `CONFLICT` phơi mọi position; `status=insufficient_evidence` khi không claim nào đạt FACT; `grounding_summary.calibration_status=uncalibrated`. **Enforce server-side — không tin Claude tự giác.** | FR-021/AC-001 (GT-1 UNKNOWN), FR-021/AC-002 (GT-2 FACT), FR-021/AC-003 (GT-4 CONFLICT), FR-021/AC-004 (GT-3 missing provenance), FR-021/AC-005 (GT-6 confidence proxy), FR-021/AC-006 (GT-5/GT-7 single gate + provenance); NFR-008, NFR-009 (ADR-0018) | T-098, T-104, T-105 | `packages/mcp_knowledge/src/mcp_knowledge/pack/assembler.py` (hoàn thiện), `.../grounding/{verdict,confidence}.py` | 3–5 | Verdict đóng server-side ở **một** gate SAU permission; no-evidence⇒UNKNOWN+message cố định; confidence là `retrieval×agreement×freshness` deterministic, `confidence_basis` nhãn + `uncalibrated`; CONFLICT phơi mọi position; `insufficient_evidence` khi 0 FACT |
| T-107 | BE | E8 — GT-1..GT-7 contract test (adversarial, độc lập ngưỡng) | Bộ test khớp ADR-0018 §6: **GT-1** no-source→UNKNOWN+message cố định; **GT-2** source→FACT 6-field, `evidence` resolve qua `kb_get_document` về đúng chunk; **GT-3** thiếu field provenance → không bao giờ FACT; **GT-4** mâu thuẫn→CONFLICT phơi mọi position + `authority_note`; **GT-5** structural single gate (0 đường vòng); **GT-6** confidence là proxy nhãn, không path nào biến confidence cao thành FACT; **GT-7** compression giữ provenance. Mọi assert FACT/UNKNOWN/CONFLICT **chạy ngay** (độc lập ngưỡng τ). | FR-021/AC-001..006 (toàn bộ GT-1..GT-7, ADR-0018 §6, L-001/L-002); NFR-008, NFR-009 | T-106, T-104 | `packages/mcp_knowledge/tests/test_grounding_gt.py` | 2–4 | 7 nhóm GT xanh; GT-2 `evidence` resolve về đúng chunk qua `kb_get_document`; GT-5 single-gate structural đỏ nếu có đường vòng; không assert nào phụ thuộc τ (chạy khi egress HF còn chặn) |
| T-108 | BE | E8 — eval/telemetry grounding + ngưỡng τ TBD (ghi rõ cho khi gỡ egress HF) | `eval/grounding/` harness + telemetry (đếm verdict/claim, reranker enabled/disabled, latency gate); bộ số khởi tạo `τ_fact=0.6/τ_low=0.3` **chỉ cho infrastructure test**, envelope `calibration_status=uncalibrated`; **comment `# THRESHOLD TBD (NFR-010, L-002) — chốt khi gỡ egress HF chạy golden-set thật`** cạnh mỗi hằng số τ để QA grep; harness sẵn sàng chạy golden-set khi egress mở (nối B9/NFR-003/DK1). | FR-021/AC-005 (NFR-010 calibration UNVERIFIED, L-002); NFR-003 (nối DK1/D-002) | T-107 | `eval/grounding/{harness.py,questions.yaml}`, `docs/claude-usage/company-knowledge.md` | 1–3 | Harness chạy được với provider fake deterministic; τ là hằng số **có comment TBD** grep được; envelope `uncalibrated`; tài liệu ghi rõ τ chốt khi gỡ egress HF + không khẳng định recall thật (NFR-003 UNVERIFIED) |

### E7 — Gateway-boundary in-process (routing/auth-context/rate-limit/audit, giữ stdio), dep E4,E6

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-109 | BE | E7 — `mcp_gateway` in-process: routing + auth-context + rate-limit + audit (0 cổng mạng) | `packages/mcp_common/.../gateway.py` (hoặc `mcp_gateway/`): boundary **in-process** (không mở socket, giữ stdio/NFR-005): routing/discovery tới Knowledge+Live server, gắn auth-context (chủ process ở v1, đặt sẵn per-request slot cho v1.1), rate-limit **token-bucket** (vượt → `rate_limited` + `retry_after_s`, có audit), audit **append-only ra stderr** (không stdout, redaction áp, query chỉ ở DEBUG); để sẵn **transport-adapter seam** cho HTTP v1.1 (không implement). | FR-022/AC-001, FR-022/AC-002, FR-022/AC-003 (EB-005/BR-012, ADR-0021); NFR-012 | T-104, T-102 | `packages/mcp_common/src/mcp_common/gateway.py`, `tests/test_gateway_routing.py`, `tests/test_gateway_ratelimit.py`, `tests/test_gateway_no_port.py` | 3–5 | Route đúng server + audit append-only ra stderr với identity; vượt rate-limit → `rate_limited`+`retry_after_s` + audit; **test: process không mở listening socket nào** (stdio only); audit/log không ra stdout (guard như NFR-005) |

### Sign-off CHG-001

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + tool) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|----|
| T-110 | BE | Sign-off CHG-001 — tool surface 61 + read-only + grounding + stdio toàn tầng mới | Snapshot **61 tool mặc định (62 khi `MCP_OPENSEARCH_ALLOW_DSL`)** so `api-contract.yaml` (48 nền + 13 mới); `assert_readonly_tool_surface` trên `mcp-knowledge` + `mcp-jira` + phủ method connector Jira gọi; unknown-write-tool JSON-RPC cho 2 server mới; **GT-1..GT-7 xanh**; single-choke-point (perm #1, gate #2) structural; gateway no-port; `doctor` 2 server mới; đăng ký Claude Desktop (NFR-005); eval harness τ=TBD ghi rõ. | FR-016..022 regression (EB-001/002/004/005); NFR-006..012; FR-014/AC-001/002 | T-094, T-103, T-106, T-107, T-108, T-109 | `docs/signoff/chg001.md`, `scripts/verify_tool_surface.py` (cập nhật 61/62) | 1–2 | 61 tool mặc định (62 flag) khớp contract, 0 tool ghi trên 13 tool mới; GT-1..GT-7 xanh; perm #1 trước gate #2, mỗi cái một choke point; gateway 0 cổng mạng; 2 server hiện trong Claude; `HF_HUB_OFFLINE=1` + 0 egress xác nhận |

## Parallelism — CHG-001

Không có FE. Song song giữa các họ task BE độc lập, **sau khi E1 xanh**:

| Có thể chạy song song | Điều kiện | Ghi chú |
|---|---|---|
| **T-089 (DK3/env) ∥ chuẩn bị skeleton E2/E3** | T-088 xong | DK3 là tài liệu + env; không chặn code. |
| **E2 (T-091…T-094) ∥ E3 (T-095…T-098)** | E1 (T-088) xong | Jira server/connector độc lập hoàn toàn với Hybrid-RAG. Đây là cửa sổ song song lớn nhất của CHG-001 (2 luồng dài). |
| **Trong E3: T-095 (hybrid) → T-097 (rerank) tuần tự; T-096 (envelope) ∥ T-095** | — | Envelope chỉ phụ thuộc `mcp_common.envelope` nền; chạy song song retrieval. |
| **E4 là điểm hợp lưu** (cần E2 **và** E3) | T-098 + T-092 xong | `get_jira_context`/`search_company_knowledge` cần cả Jira live + pipeline E3. |
| **E5 (T-105) ∥ E7 (T-109)** | E6 (T-104) + E4 xong; E5 thêm E2 | Live-vs-Knowledge và gateway không phụ thuộc nhau. |
| **E6 (T-104) chặn E8 (T-106)** | T-102 xong | Permission (#1) phải có **trước** grounding gate (#2) trong đường đi — không song song, tuần tự bắt buộc. |

**Đường tuần tự dài nhất (critical path CHG-001):**
`T-087 → T-088 → T-095 → T-096 → T-097 → T-098 → (T-091→T-092 cho E4) → T-099 → T-100 → T-102 → T-104 (E6 perm) → T-105 (E5) → T-106 (E8 gate) → T-107 → T-110`.
Rút ngắn: chạy **E2 (Jira) song song E3** ngay sau E1; chạy **E7 gateway song song E5** sau E6.

## Milestones vs baseline — CHG-001

> **Baseline cost/schedule của CHG-001 CHƯA chốt ở Gate 1** (plan-approval.md: "Baseline cost/schedule
> của CHG-001 chưa chốt — chờ options của SA"; D-001 escalate). Vì không có baseline go-live date tường
> minh cho CHG-001, `schedule_delta_pct` được tính **so với tổng estimate của chính CHG-001** (không phải
> so một baseline date đã duyệt), và đánh dấu **baseline absent** để CTO chốt khi options cost/schedule ra.

| Milestone (epic) | Baseline date (plan-approval.md) | Planned (từ estimate high-end) | Delta % |
|---|---|---|---|
| E1 (schema + DK2) | *absent (chưa chốt Gate 1)* | T-087..T-090 = 3+4+1+2 = **10 agent-h** | n/a |
| E2 (Jira) ∥ E3 (Hybrid-RAG) | *absent* | max(E2=4+4+4+3=15, E3=5+3+4+4=16) = **16 agent-h** (song song) | n/a |
| E4 (Knowledge 8 tool) | *absent* | 4+4+4+5+3 = **20 agent-h** | n/a |
| E6 (permission) | *absent* | **5 agent-h** | n/a |
| E5 (live-vs-knowledge) ∥ E7 (gateway) | *absent* | max(E5=4, E7=5) = **5 agent-h** | n/a |
| E8 (grounding gate + GT + eval) | *absent* | 5+4+3 = **12 agent-h** | n/a |
| Sign-off | *absent* | **2 agent-h** | n/a |

**Số học `schedule_delta_pct`:** baseline go-live date cho CHG-001 = **absent** ⇒ công thức
`(planned − baseline)/baseline × 100` **không xác định**. Tổng estimate high-end (critical path, có
tận dụng song song E2∥E3 và E5∥E7) ≈ `10 + 16 + 20 + 5 + 5 + 12 + 2 = 70 agent-h`; tổng high-end **không
song song** = `10+15+16+20+5+4+5+12+2 = 89 agent-h`. CTO dùng con số này làm baseline đề xuất khi chốt
cost/schedule ở plan-review; cho tới đó `schedule_delta_pct = n/a (baseline absent)`.

## Release path — CHG-001 (dev → UAT → PRE → CAB → PROD → watch)

Giữ đúng `squad-env-promotion` của nền, **cộng** các điều kiện mới của CHG-001:

| Cổng | Phải done trước |
|---|---|
| **dev** | T-087 (migration-locking fix) xanh **trước** mọi migration 0007+; E1..E8 unit/integration xanh trên compose. |
| **UAT/PRE** (n/a-shared theo thiết kế, chạy PRE-equivalent trên host dev + Docker) | GT-1..GT-7 xanh; adversarial permission test (T-104) xanh; single-choke-point structural (perm #1 trước gate #2); gateway no-port; `db upgrade 0007→0008` trên bản copy có dữ liệu không khoá full-table. **DK3:** Redis ACL least-privilege per-env đã ghi env-promotion (T-089) — **không** tái dùng dev ACL. |
| **CAB** (Gate 2 CEO) | caveat **NFR-003/NFR-010 τ UNVERIFIED** (egress HF) mang lên CEO như residual risk đã-chấp-nhận (nối D-002 ĐK1); 0 tool ghi trên 13 tool mới; backup-check trước migration prod. |
| **PROD → watch** | migration-locking an toàn trên **populated** prod (DK2 — không còn empty-table như go-live đầu); rollback: gỡ 2 server mới + gateway entry + (nếu cần) `db downgrade`/giữ bia mộ; observation window + SLI như nền. |

## Definition of Done — CHG-001

Kế thừa toàn bộ DoD nền (10 mục) + bổ sung:

11. **Grounding bất biến (BR-007/BR-008/BR-010):** mọi claim ra khỏi server đi qua **đúng một** grounding
    gate (GT-5); no-evidence ⇒ `UNKNOWN` + message cố định; CONFLICT phơi mọi position; confidence mang
    `confidence_basis` nhãn, `calibration_status=uncalibrated`, không path nào biến confidence→FACT (GT-6).
12. **Permission bất biến (BR-009):** `enforce_permission()` một choke point default-deny **trước** gate;
    adversarial test (restricted-không-grant) xanh; 0 rò vào candidate/pack/citation.
13. **No-egress/stdio (BR-011/BR-012):** `HF_HUB_OFFLINE=1`; 0 outbound socket khi grounding/rerank;
    gateway/orchestrator in-process, 0 listening socket; audit/log chỉ stderr.
14. **Read-only mới (BR-006/NFR-006):** 13 tool mới `x-readonly`/`x-side-effects: none`, startup check
    từ chối serve nếu role ghi được; Jira 0 create/transition/comment.

## Risks & mitigations — CHG-001

| # | Rủi ro | Task xử lý | Giảm thiểu / ghi chú |
|---|---|---|---|
| R-C1 | Migration 0007+ khoá full-table trên prod đã có dữ liệu (DK2 chưa fix) | **T-087**, T-088, T-090 | T-087 fix runner **trước** mọi migration; `NOT VALID`+`VALIDATE` riêng, `CONCURRENTLY` ngoài txn, `lock_timeout`. Đây là ĐK2 của D-002, chặn trước E1. |
| R-C2 | Hai grounding gate song song (fallback tạm không được gỡ) | **T-106**, T-107 (GT-5) | Plan **không** dùng fallback D-004 §3: assembler (E3) xây trước, gate cắm thẳng; GT-5 structural assert **một** gate. |
| R-C3 | Permission rò tài liệu restricted vào context-pack | **T-104** | Default-deny + adversarial test (FR-019/AC-002) + structural single-choke-point (AC-003); perm chạy **trước** retrieval ranking. |
| R-C4 | Reranker weights thiếu → âm thầm hạ chất lượng | **T-097** | RRF-only fallback **sau cờ tường minh** + `reranker=disabled` trong `grounding_summary` (L-002), không giả vờ full-quality. |
| R-C5 | Egress HF/network trong rerank/embed/grounding (vi phạm vendors=none) | T-097, T-108, **T-110** | `HF_HUB_OFFLINE=1` assert; test 0 outbound socket; confidence deterministic không gọi LLM/API. |
| R-C6 | Ngưỡng τ bị bịa số (đọc như bằng chứng) | **T-108** | τ=TBD, `uncalibrated`, bộ số `0.6/0.3` chỉ cho infra test có comment grep được; chốt khi gỡ egress HF (NFR-010, L-002). |
| R-C7 | B4 đến trước module assembler ⇒ phải cắm tạm vào `mcp_pgvector` | — (sequencing) | Plan xếp E3 assembler **trước** E8 nên **không** cần fallback. Nếu lịch buộc đảo: áp D-004 §3a (ghi việc DỜI) + §3b (gỡ gate tạm, GT-5) + §3c (confidence chỉ retrieval) — là điều kiện Done, không tuỳ chọn. |
| R-C8 | Gateway in-process vô tình mở cổng mạng (phá NFR-005) | **T-109** | Test khẳng định 0 listening socket + audit chỉ stderr; transport-adapter seam để sẵn **không** implement HTTP. |
| R-C9 | Jira Cloud vs Server/DC khác endpoint/cursor | **T-091** | Split đóng trong `client.py` (`/api/3`+`nextPageToken` vs `/api/2`+`startAt`); cursor opaque; test cả hai flavor. |

## Coverage matrix — CHG-001 AC → task

| FR / AC | Task phủ |
|---|---|
| FR-016/AC-001 | T-095, T-096, T-098, T-102 |
| FR-016/AC-002 | T-102, T-103 |
| FR-016/AC-003 | T-097 |
| FR-016/AC-004 | T-095, T-097, T-099, T-103 |
| FR-017/AC-001 | T-091, T-092 |
| FR-017/AC-002 | T-092 |
| FR-017/AC-003 | T-091, T-094 |
| FR-017/AC-004 | T-093 |
| FR-018/AC-001 | T-105 |
| FR-018/AC-002 | T-105 |
| FR-018/AC-003 | T-105 |
| FR-019/AC-001 | T-104 |
| FR-019/AC-002 | T-104 |
| FR-019/AC-003 | T-104 |
| FR-020/AC-001 | T-100, T-101 |
| FR-020/AC-002 | T-100 |
| FR-020/AC-003 | T-101 |
| FR-021/AC-001 | T-106, T-107 |
| FR-021/AC-002 | T-106, T-107 |
| FR-021/AC-003 | T-106, T-107 |
| FR-021/AC-004 | T-106, T-107 |
| FR-021/AC-005 | T-106, T-107, T-108 |
| FR-021/AC-006 | T-098, T-106, T-107 |
| FR-022/AC-001 | T-109 |
| FR-022/AC-002 | T-109 |
| FR-022/AC-003 | T-109 |

**26 AC id mới (FR-016:4, FR-017:4, FR-018:3, FR-019:3, FR-020:3, FR-021:6, FR-022:3 = 26) / 26 được phủ.
`uncovered_ac` rỗng.**

| NFR (CHG-001) | Task phủ |
|---|---|
| NFR-006 | T-091, T-094, T-099, T-103, T-110 |
| NFR-007 | T-104 |
| NFR-008 | T-106, T-107, T-110 |
| NFR-009 | T-106, T-107, T-110 |
| NFR-010 | T-108 (τ TBD, UNVERIFIED — nối NFR-003/DK1) |
| NFR-011 | T-089, T-097, T-108, T-110 |
| NFR-012 | T-087, T-109, T-110 |

## Epic → task map (CHG-001)

| Epic | Task | Chặn / phụ thuộc |
|---|---|---|
| **E1** | T-087 (migration-locking **đầu tiên**), T-088 (0007/0007b/0008), T-089 (DK3/env), T-090 (db upgrade verify) | **CHẶN trước mọi epic** (DK2 chạy trên pgvector đã có dữ liệu) |
| **E2** | T-091, T-092, T-093, T-094 | dep E1 |
| **E3** | T-095, T-096, T-097, T-098 | dep E1 |
| **E4** | T-099, T-100, T-101, T-102, T-103 | dep E1, E2, E3 |
| **E6** | T-104 | dep E1, E4 — **trước E8** |
| **E5** | T-105 | dep E2, E4 |
| **E8** | T-106, T-107, T-108 | dep E3, E4, E6 (permission trước gate) |
| **E7** | T-109 | dep E4, E6 |
| Sign-off | T-110 | dep tất cả |

## Ghi chú cho qa-plan (CHG-001)

- **GT-1..GT-7 là contract test chạy NGAY** (độc lập ngưỡng τ) — QA viết ở cùng cấp E-001..E-003 của L-001
  (D-004 C4). Assert FACT/UNKNOWN/CONFLICT không chờ egress HF. Map: GT-1→FR-021/AC-001, GT-2→AC-002,
  GT-3→AC-004, GT-4→AC-003, GT-5+GT-7→AC-006, GT-6→AC-005.
- **Adversarial permission test (FR-019/AC-002)** là bắt buộc, không phải tùy chọn (L-001): nạp đúng query
  khớp một document `restricted` không-grant, assert **không** rò vào candidate/context-pack/citation.
- **Single-choke-point structural** phải test **hai** choke point tách biệt: #1 `enforce_permission` (FR-019/
  AC-003) và #2 grounding gate (FR-021/AC-006 GT-5); và permission chạy **trước** gate — không trộn thành một.
- **FR-014 AC-002 ở tầng JSON-RPC** (Q-L3 nền): unknown write-tool → JSON-RPC error, **không** `ErrorEnvelope`
  — áp cho `mcp-knowledge` + `mcp-jira` (dùng lại helper T-014).
- **Ngưỡng τ_fact/τ_low = TBD/UNVERIFIED** (NFR-010/L-002): QA **không** assert giá trị verdict dựa trên τ;
  chỉ assert bất biến (no-evidence⇒UNKNOWN, conflict⇒CONFLICT, missing-provenance⇒không-FACT) + `calibration_status=uncalibrated`.
  Grep `# THRESHOLD TBD` để tìm mọi chỗ số tạm. NFR-003 recall thật vẫn UNVERIFIED (egress HF, nối D-002 ĐK1).
- **Live test Jira** gắn `@pytest.mark.live`; nếu Jira unreachable (VPN/credential) → skip và **phải nêu trong
  regression-report** (như nền), không lặng lẽ bỏ qua.

---

HANDOFF
feature: mcp-data-platform
status: done
artifacts: [implementation-plan.md]
counts: BE=24 FE=0 other=0   # task MỚI CHG-001 (T-087..T-110); nền T-001..T-086 không đổi (T-067 đã đóng)
new_task_range: T-087..T-110 (24 task mới)
tool_surface: 61 mặc định (62 khi MCP_OPENSEARCH_ALLOW_DSL=true) = 48 nền + 13 mới (8 Knowledge + 5 Jira)
epic_task_map:
  E1: [T-087, T-088, T-089, T-090]   # T-087 migration-locking = task ĐẦU TIÊN, chặn trước mọi epic (DK2)
  E2: [T-091, T-092, T-093, T-094]   # dep E1
  E3: [T-095, T-096, T-097, T-098]   # dep E1
  E4: [T-099, T-100, T-101, T-102, T-103]  # dep E1,E2,E3
  E6: [T-104]                        # dep E1,E4 — TRƯỚC E8
  E5: [T-105]                        # dep E2,E4
  E8: [T-106, T-107, T-108]          # dep E3,E4,E6 (permission TRƯỚC grounding gate)
  E7: [T-109]                        # dep E4,E6
  signoff: [T-110]
blocking_tasks:
  - T-087 (DK2 migration-locking) là task ĐẦU TIÊN tuyệt đối — chặn T-088 và qua đó CHẶN TRƯỚC MỌI epic CHG-001 (schema chạy trên pgvector đã go-live, D-002 ĐK2 / D-003 DK2).
  - E1 (T-088) chặn E2, E3, E4, E6.
  - E6 permission (T-104) chặn E8 grounding gate (T-106) — permission choke point #1 phải TRƯỚC grounding gate #2 trong đường đi (ADR-0018, không hai gate song song).
uncovered_ac: []   # 26 AC mới (FR-016..022) / 26 phủ
schedule_delta_pct: n/a (baseline absent — CHG-001 cost/schedule chưa chốt ở Gate 1 per plan-approval.md/D-001)
estimate_total_agent_h: ~70 (critical path, tận dụng E2∥E3 + E5∥E7) / ~89 (không song song) — CTO chốt baseline ở plan-review
invariants_kept: read-only (0 write/13 tool mới), stdio (gateway in-process, 0 cổng mạng), vendors=none + 0 egress (HF_HUB_OFFLINE=1), permission server-side TRƯỚC grounding gate
thresholds_tbd: τ_fact/τ_low (NFR-010, L-002) — bộ số khởi tạo 0.6/0.3 chỉ cho infra test, calibration_status=uncalibrated, chốt khi gỡ egress HF chạy golden-set thật (nối NFR-003/DK1/D-002 ĐK1); comment `# THRESHOLD TBD` grep được (T-108)
qa_notes:
  - GT-1..GT-7 chạy NGAY (độc lập ngưỡng); map GT→FR-021 AC ở "Ghi chú cho qa-plan".
  - FR-019 adversarial permission test bắt buộc (restricted-không-grant → 0 rò candidate/pack/citation).
  - Hai choke point tách biệt: perm #1 (FR-019/AC-003) + gate #2 (FR-021/AC-006 GT-5); perm TRƯỚC gate.
  - FR-014 AC-002 assert ở tầng JSON-RPC cho 2 server mới (không ErrorEnvelope).
  - NFR-003 recall thật + τ vẫn UNVERIFIED (egress HF) — QA chỉ assert bất biến + calibration_status=uncalibrated.
out_of_scope (records/backlog.md): B2 Knowledge Model đầy đủ, B5 Memory, B6 State, B7 Provenance đầy đủ, B8 Governance, B9 Evaluation (100 Q), B10 Observability đầy đủ, Orchestrator/Harness — KHÔNG task nào cho các block này; chỉ B4 Grounding (D-004 in-scope) được hiện thực ở E8.

---

# CHG-003 — Real ingestion + real egress, CLI-driven, 9-source runbook (Confluence first)

> **Nguồn đầu vào CHG-003:** `2-gate1/plan-approval.md` (CHG-003 section — Option B, kept invariants),
> `docs/adr/0023-chg003-egress-ingestion-deviation.md` (§6a allow-list default-deny, §6c credential,
> §6d HF model, §6e 4 adversarial tests), `3-spec/requirements.md` (FR-023..027, NFR-013..014, 17 AC),
> `4-design/architecture.md` Appendix A (9-source runbook) + api-contract.yaml `ingest_run.x-egress`,
> `records/decisions.md` D-006 (lean track, deviation ~86/100), `knowledge/lessons.md` L-001/L-002.
>
> **Lean track (D-006):** năng lực đã build + review ở nền + CHG-001; CHG-003 là **thay đổi
> ranh giới policy + vận hành**, không phải thiết kế lại. Mọi task mới `x-change: CHG-003`,
> **Owner = BE** (không FO/FE). Đánh số tiếp từ T-110 → **T-111..T-123** (không đánh số lại nền).
> CHG-001 vẫn **postponed** (D-006 C6); task CHG-003 build trên code đã có, không chạm epic E1..E8.

## Summary & sequencing strategy — CHG-003

Chiến lược: **một choke point egress trước, Confluence end-to-end thật thứ hai, rồi tổng quát hoá**.
Đây là áp dụng trực tiếp L-001 (một choke point + adversarial test) cho đúng cái bất biến đang mở
(no-egress → default-deny allow-list).

1. **Egress guard là walking skeleton của CHG-003 — làm TRƯỚC, nó chặn mọi pull thật.** Một module
   `mcp_common/egress.py` **mới** (không tồn tại trên đĩa — đã audit: `mcp_common/` có `http.py`,
   `config.py`, `readonly.py`, `redact.py`, `gateway.py` nhưng **không** có egress/allow-list). Nó là
   **một** structural choke point default-deny cắm vào `mcp_common.http.build_client` (transport hook
   đã có cho GET/HEAD — ADR-0003 A2) + đường tải model. 4 adversarial test ADR-0023 §6e là điều kiện
   Done, viết **cùng** task guard (L-001: guarantee + adversarial test đi liền, không để prose-only).
2. **Confluence Cloud thật là slice end-to-end thứ hai** (sau guard): `ConfluenceClient` đã có
   `credential_check` + `*_FILE` loading + `doctor` (đã audit `client.py`), connector đã có
   (`connectors/confluence.py`, registry = {confluence,gitlab,opensearch,jira}). Task CHG-003 **không
   viết lại** chúng — nó (a) point tới `https://tnexwm.atlassian.net` qua env/`*_FILE`, (b) chứng minh
   `doctor` từ chối account ghi được trên Cloud flavor, (c) gate live run sau env flag, (d) verify
   doctor→run→status→`kb_semantic_search` bằng **fixture + fake token** (CI không cần creds thật).
3. **Tổng quát hoá sang GitLab/OpenSearch/Jira** chỉ là point mỗi connector qua guard + doctor
   read-only; cùng khuôn Confluence. OpenSearch giữ mặc định TẮT (allow-list index — ADR-0012 A5).
4. **Model download thật là một task riêng, tách rời egress Atlassian** (ADR-0023 §6d, D-006 §3d/C5):
   `HF_HUB_OFFLINE` chỉ flip online cho bước tải, rồi offline cho serving; load real model sau provider
   port đã có (`embedding/local.py`, `embedding/config.py`); giữ `DeterministicFakeProvider`
   (`embedding/fake.py`) làm default/test path. **Không bịa recall/τ** (L-002, ADR-0010 vẫn provisional).
5. **Runbook validation** làm các lệnh Appendix A chạy được thật cho cả 9 nguồn (4 ingestable + 5
   live-only), mỗi nguồn map tới `mcp-<src> doctor` của nó.
6. **Secret-handling** là task riêng: token env/`*_FILE` only, scrub cả 2 chiều, `.env.example`
   placeholders — không bao giờ token thật.

> **DK2 migration-locking (E1 T-087) đã done** — CHG-003 **không thêm schema**. Nếu một task CHG-003
> buộc phải thêm schema (không dự kiến), nó **phải** theo khuôn DK2 (`NOT VALID`+`VALIDATE` riêng,
> `CREATE INDEX CONCURRENTLY` ngoài txn) và được gọi ra tường minh trong "Done when".

### Slice end-to-end CHG-003 (walking skeleton)

**T-111 (egress guard + 4 adversarial test) → T-113 (Confluence Cloud enablement) → T-118 (runbook
Confluence end-to-end) → T-120 (secret-handling).**

Điều kiện kết thúc slice: với **fake/stub token + fixture ghi lại**, `make ci` xanh toàn bộ —
egress guard từ chối host ngoài allow-list, cho qua `tnexwm.atlassian.net`; `mcp-confluence doctor`
chứng minh read-only; `mcp-ingest run --source confluence` (fixture) → `status` → `kb_semantic_search`
trả citation `tnexwm.atlassian.net`; token không xuất hiện trong log/stdout/result. Live run thật
(token thật) **gate sau env flag** — là bước vận hành CEO chạy, **không** phần của `make ci`.

## Patterns to mirror — CHG-003

Brownfield; mirror code đã có (file:ref đã audit trên đĩa 2026-10-02):

- **Transport allowlist + GET/HEAD hook:** `packages/mcp_common/src/mcp_common/http.py`
  (`build_client`, `request_with_retry`) — egress guard cắm vào đây, cùng tầng với assertion
  transport read-only (ADR-0003 A2). Guard là **lớp mới** phía ngoài, không thay thế hook cũ.
- **Credential read-only check + `*_FILE`:** `packages/mcp_confluence/src/mcp_confluence/client.py`
  (`ConfluenceClient.credential_check`, `CredentialReport`, `_WRITE_OPERATIONS`, `OP_CURRENT_USER`
  startup-only), `mcp_common/config.py` (`*_FILE` convention). Jira dùng cùng khuôn
  (`packages/mcp_jira/.../client.py`).
- **Scrub 2 chiều:** `packages/mcp_common/src/mcp_common/redact.py` (`scrub`) + đường lỗi/log
  `mcp_common/errors.py`, `mcp_common/logging.py` (stderr-only, stdout guard).
- **Connector registry + pull path:** `packages/mcp_ingest/src/mcp_ingest/connectors/registry.py`
  (`REGISTRY` = {confluence,gitlab,opensearch,jira}), `connectors/confluence.py`, `pipeline/run.py`,
  `cli.py` (`run|status|sources|reembed|prune`, `db upgrade`).
- **Embedding provider port:** `packages/mcp_ingest/src/mcp_ingest/embedding/` — `local.py`
  (`LocalSentenceTransformerProvider`), `fake.py` (`DeterministicFakeProvider`), `config.py`,
  `validate.py` (dimension/model guard), `ports.py`. ADR-0010 provisional `bge-m3` 1024d.
- **Doctor/health mỗi package:** `cli.py` subcommand `doctor` ở cả 9 server + `mcp-ingest`.

## Setup tasks — CHG-003

Không có schema/scaffolding mới (DK2 đã done, store đã có). Setup = cấu hình + `.env.example`:

- Biến env mới (chỉ thêm, không phá): `MCP_EGRESS_ALLOWLIST` (CSV host, default **rỗng** ⇒
  default-deny), `MCP_CONFLUENCE_BASE_URL`/`MCP_CONFLUENCE_EMAIL`/`MCP_CONFLUENCE_API_TOKEN(_FILE)`,
  `MCP_CONFLUENCE_FLAVOR=cloud`, `MCP_INGEST_ALLOW_LIVE_EGRESS` (gate live run, default `false`),
  `HF_HUB_OFFLINE` (default `1`; flip `0` **chỉ** trong bước tải model). Ghi vào `.env.example` với
  **placeholder** (T-120). Không file compose/migration mới.

## Tasks — CHG-003

### Epic CE1 — Egress guard (default-deny choke point + 4 adversarial test) · làm TRƯỚC

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-111 | BE | Egress guard: một default-deny allow-list choke point cho PULL + model download (L-001) | **Module MỚI** `mcp_common/egress.py` (không tồn tại — audit confirmed): `check_egress(host)` default-deny, allow-list từ `MCP_EGRESS_ALLOWLIST` (hỗ trợ wildcard `*.atlassian.net`), host ngoài list → raise `EgressDenied` (map `not_permitted`). Cắm vào `mcp_common.http.build_client` (transport hook, cùng chỗ GET/HEAD ADR-0003 A2) **và** đường tải model (T-119). **Một** choke point mọi outbound đi qua. **4 adversarial test ADR-0023 §6e viết CÙNG task (L-001):** (#1) default-deny — host không-list bị **refuse** không im lặng cho qua; (#2) allow-list honoured — `tnexwm.atlassian.net` qua, host chưa-config bị deny. Để tránh regression NFR-005/012: test khẳng định 9 MCP server + Jira **không** mở outbound ngoài upstream read API của chúng và **không** mở cổng mạng. | FR-024/AC-001, FR-024/AC-002, FR-024/AC-003 (ADR-0023 §6a/§6e#1/#2/#4); NFR-013, NFR-012 regression | — (CHG-003 đầu tiên, chặn pull thật) | `packages/mcp_common/src/mcp_common/egress.py`, `packages/mcp_common/src/mcp_common/http.py` (wire), `packages/mcp_common/tests/test_egress_default_deny.py`, `packages/mcp_common/tests/test_egress_allowlist.py`, `packages/mcp_common/tests/test_servers_no_egress_no_port.py` | 2–3 | `MCP_EGRESS_ALLOWLIST` rỗng ⇒ mọi outbound bị deny; set `*.atlassian.net` ⇒ `tnexwm.atlassian.net` qua, host khác deny (adversarial test xanh); 9 server + Jira: 0 outbound ngoài upstream, 0 listening socket; guard là **một** choke point (grep: mọi đường pull/model qua `check_egress`) |
| T-112 | BE | Wire ingest PULL path + model-download path qua egress guard (connectors unchanged otherwise) | Point 4 connector ingestable (`connectors/confluence.py`, `gitlab.py`, `opensearch.py`, `jira.py`) + `pipeline/run.py` qua `build_client` đã-có-guard; **không** đổi logic connector. Đường tải model (T-119) cũng qua guard. Khẳng định: 9 MCP server (stdio) **không** đổi — chúng chỉ chạm upstream read API của chính mình, giữ stdio; egress là thuộc tính của `mcp-ingest` pull + model download, không phải server trả client. | FR-024/AC-003, FR-023/AC-003 regression (EB-006, ADR-0023 §6a); NFR-013 | T-111 | `packages/mcp_ingest/src/mcp_ingest/connectors/*.py` (wire client build), `packages/mcp_ingest/src/mcp_ingest/pipeline/run.py`, `packages/mcp_ingest/tests/test_egress_wired.py` | 1–2 | Mọi pull connector + model download đi qua guard; test: pull tới host ngoài allow-list bị refuse ở connector; server stdio/upstream-only không đổi (regression xanh) |

### Epic CE2 — Confluence Cloud real-ingest enablement (FIRST) + tổng quát hoá

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-113 | BE | Confluence Cloud thật: point `ConfluenceClient` tới `https://tnexwm.atlassian.net` qua env/`*_FILE` | Dùng lại `ConfluenceClient` + `credential_check` + `*_FILE` loading đã có (audit `client.py`); CHG-003 chỉ cấu hình `MCP_CONFLUENCE_BASE_URL=https://tnexwm.atlassian.net`, `MCP_CONFLUENCE_FLAVOR=cloud`, token qua `MCP_CONFLUENCE_API_TOKEN_FILE`. Khẳng định allow-list chứa `*.atlassian.net`. **Không** đổi tool surface. Test bằng **fixture + fake token** (respx), không creds thật. | FR-023/AC-001, FR-025/AC-001 (ADR-0023 §6c, ADR-0007); FR-024/AC-001 | T-111, T-112 | `packages/mcp_confluence/src/mcp_confluence/settings.py` (confirm base_url/flavor), `packages/mcp_confluence/tests/test_cloud_target.py` | 1–2 | `doctor` (fixture) báo config ok tới `tnexwm.atlassian.net` flavor cloud; token từ `*_FILE`; egress `*.atlassian.net` cho qua; không creds thật trong test |
| T-114 | BE | `doctor` từ chối account write-capable trên Confluence Cloud (read-only gate) | Chứng minh `credential_check` (`_WRITE_OPERATIONS` đã có) phủ **Cloud** flavor: account có bất kỳ write op → `build_server()`/`doctor` **từ chối serve**, nêu tên write op. Escape `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` log WARN mỗi start (không dùng cho token thật). Fixture cả hai nhánh (read-only ok / write-capable refused). | FR-025/AC-003 (ADR-0023 §6c/§6e#4, ADR-0003 A1/ADR-0007 A2); NFR-014 | T-113 | `packages/mcp_confluence/src/mcp_confluence/client.py` (confirm Cloud coverage), `packages/mcp_confluence/tests/test_credential_readonly_cloud.py` | 1–2 | Fixture read-only → doctor ok; fixture write-capable → refuse to serve + write op được nêu; escape hatch log WARN |
| T-115 | BE | Confluence end-to-end (fixture): doctor → ingest run → status → verify via `kb_semantic_search` | Chạy đường pull-ingest-verify đầy đủ với **fixture Confluence Cloud + fake embedding provider** (DeterministicFakeProvider): `mcp-ingest run --source confluence` ghi `kb.*` dưới `mcp_ingest_rw`, `status` cho `last_success_at`/counts > 0, `kb_semantic_search` trả `status=ok` + citation `source_uri` resolve `tnexwm.atlassian.net`. Negative: source unreachable → `partial`/`failed`, checkpoint không advance, không corrupt corpus, không bịa (carry FR-012 AC-002). Regression read-only-to-source: pull chỉ GET/HEAD tới source, ghi chỉ `kb.*` (adversarial #4). | FR-023/AC-001, FR-023/AC-002, FR-023/AC-003 (ADR-0023 §6e#4); NFR-013/NFR-014 | T-112, T-113, T-114 | `packages/mcp_ingest/tests/test_confluence_cloud_e2e.py`, `packages/mcp_ingest/tests/fixtures/confluence_cloud/` | 2–3 | Fixture e2e xanh: doctor→run→status→verify; citation resolve `tnexwm.atlassian.net`; unreachable→partial không corrupt; pull 0 write tới source, ghi chỉ `kb.*` |
| T-116 | BE | Tổng quát hoá: GitLab / OpenSearch / Jira cùng khuôn doctor→run→status→verify qua guard | Áp đúng shape T-113..T-115 cho 3 connector còn lại trong registry (fixture + fake token mỗi nguồn). OpenSearch giữ **mặc định TẮT** (chỉ ingest index trong `MCP_INGEST_OPENSEARCH_INDICES`, ADR-0012 A5). Mỗi nguồn: host của nó vào allow-list **chỉ khi config** (default-deny host chưa-config). Không relax egress/read-only per-connector. | FR-023/AC-004 (ADR-0019/0012, ADR-0023 §6a); FR-026/AC-001 | T-115 | `packages/mcp_ingest/tests/test_gitlab_cloud_e2e.py`, `test_opensearch_ingest_gated.py`, `test_jira_cloud_e2e.py` | 2–3 | 3 nguồn chạy cùng shape qua guard (fixture); OpenSearch chỉ ingest allow-list index; host chưa-config bị deny; 0 write tới source |

### Epic CE3 — Real embedding-model download (separable, HF egress bounded)

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-117 | BE | `huggingface.co` vào allow-list **chỉ** cho bước tải model; tách rời egress Atlassian | Thêm `huggingface.co` (+ CDN cần thiết) vào allow-list **chỉ** khi chạy bước download; model download tách rời egress Atlassian (ingest Atlassian chạy được khi model deferred). Negative: host ngoài `huggingface.co` trên đường model → deny (default-deny, FR-024 AC-002). | FR-027/AC-002 (ADR-0023 §6d); FR-024/AC-002; NFR-013 | T-111 | `packages/mcp_common/src/mcp_common/egress.py` (model-scope allow), `packages/mcp_ingest/tests/test_model_egress_separable.py` | 1–2 | Model egress chỉ `huggingface.co`; Atlassian ingest chạy khi model deferred; host khác trên đường model bị deny |
| T-118 | BE | Controlled model download: `HF_HUB_OFFLINE` flip online **chỉ** cho tải, rồi offline cho serving | Bước download có kiểm soát: `HF_HUB_OFFLINE=0` **chỉ** trong download step rồi về `1`; load real model sau provider port đã có (`embedding/local.py`), pin ADR-0010 (provisional `bge-m3`, 1024d — **không** finalise). Giữ `DeterministicFakeProvider` (`embedding/fake.py`) làm **default/test** path. Serving sau đó embed real model với **0** outbound (serving offline). **Không** đo recall, **không** đặt τ (L-002). Live download gate sau env flag — bước CEO chạy, không phần `make ci` (CI dùng fake provider + model stub). | FR-027/AC-001, FR-027/AC-003 (ADR-0023 §6d/§7, ADR-0010 provisional, L-002); NFR-014 | T-117 | `packages/mcp_ingest/src/mcp_ingest/embedding/local.py` (download step), `packages/mcp_ingest/src/mcp_ingest/embedding/config.py`, `packages/mcp_ingest/tests/test_model_download_offline_flip.py` | 2–3 | Download: online chỉ trong bước đó, offline trước/sau; serving embed real model 0 outbound; fake provider vẫn default/test; `calibration_status=uncalibrated`, không số recall/τ bịa; ADR-0010 vẫn provisional |

### Epic CE4 — Runbook validation (9 sources: 4 ingestable + 5 live-only)

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-119 | BE | Runbook Appendix A chạy được: 4 ingestable (doctor→ingest→status→verify) | Kiểm các lệnh Appendix A.1/A.2 thực sự chạy cho Confluence/GitLab/OpenSearch/Jira với fixture: `mcp-<src> doctor` → `mcp-ingest run --source <src>` → `status` → `kb_semantic_search`. Mỗi nguồn map đúng package doctor. (Confluence là reference end-to-end — nối T-115.) | FR-026/AC-001 (ADR-0023 §A.1/§A.2); FR-023/AC-004 | T-116 | `packages/mcp_ingest/tests/test_runbook_ingestable.py`, `scripts/runbook/verify_ingestable.sh` | 1–2 | 4 nguồn ingestable chạy đúng chuỗi runbook (fixture); mỗi nguồn gọi đúng `mcp-<src> doctor`; verify qua `kb_semantic_search` |
| T-120 | BE | Runbook Appendix A.3: 5 live-only (doctor→register→`tools/list`), **không bao giờ** ingest | Kiểm CloudWatch/Kibana/Kafka/Redis/SQS-SNS: `mcp-<src> doctor` read-only ok → đăng ký `claude_desktop_config.json` (snippet `config-emit`) → `tools/list` smoke, **0 write tool**. Negative: `mcp-ingest run --source cloudwatch` **không** được chấp nhận (registry chỉ {confluence,gitlab,opensearch,jira}); "integrate" live-only = reachable+read-only+registered, **không** ingest. Regression read-only across 9 + Jira. | FR-026/AC-002, FR-026/AC-003, FR-026/AC-004 (ADR-0023 §A.3, FR-014/NFR-001); NFR-013 | T-116 | `packages/mcp_ingest/tests/test_runbook_live_only.py`, `scripts/runbook/verify_live_only.sh` | 1–2 | 5 live-only: doctor ok + registered + tools/list, 0 write tool; `mcp-ingest run --source <live-only>` bị registry từ chối; never ingested |

### Epic CE5 — Secret handling (token never leaked, both ways)

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-121 | BE | Token env/`*_FILE` only + scrub cả 2 chiều + `.env.example` placeholders (adversarial #3) | Token Atlassian chỉ qua env/`MCP_CONFLUENCE_API_TOKEN_FILE`; **không bao giờ** commit/log/return. `scrub()` áp **cả** tool/result boundary **và** error/log path (dùng lại `redact.py` + `errors.py`/`logging.py`). **Adversarial ADR-0023 §6e#3:** force một lỗi trên credential path (upstream fail nội suy credential context) → assert token **vắng** khỏi tool result **và** stderr log. `.env.example` thêm key với **placeholder** (ví dụ `MCP_CONFLUENCE_API_TOKEN_FILE=/path/to/token`), **không bao giờ** token thật; test khẳng định `.env.example` không chứa secret giống token. | FR-025/AC-002 (ADR-0023 §6c/§6e#3, ADR-0005/0015, L-001/E-003); NFR-014 | T-113 | `packages/mcp_common/src/mcp_common/redact.py` (confirm token pattern), `.env.example`, `packages/mcp_common/tests/test_token_never_leaks.py`, `packages/mcp_common/tests/test_env_example.py` (extend) | 1–2 | Forced-error trên credential path → token vắng khỏi result + stderr (adversarial xanh); `.env.example` chỉ placeholder; scrub áp 2 chiều |

### Sign-off CHG-003

| ID | Owner | Title | Mô tả | Covers (FR/AC + ADR + test) | Depends on | Files / dirs | Estimate (agent-h) | Done when |
|----|----|----|----|----|----|----|----|----|
| T-122 | BE | Sign-off CHG-003 — egress default-deny + credential + runbook + invariants kept | Tổng hợp: 4 adversarial ADR-0023 §6e xanh (#1 default-deny, #2 allow-list, #3 token-never-leaks, #4 server read-only/stdio unchanged); 9 server + Jira 0 outbound ngoài upstream + 0 cổng mạng; ingest ghi chỉ `kb.*`; token env/`*_FILE` only; Confluence Cloud end-to-end (fixture) xanh; model download offline-flip đúng; ADR-0010 vẫn provisional, `calibration_status=uncalibrated`, 0 số recall/τ bịa. Khẳng định `make ci` xanh với **fake/stub token + fixture** (live creds **không** cần cho CI). | FR-023..027 regression (EB-006); NFR-013/NFR-014; FR-014/AC-001 | T-115, T-116, T-118, T-119, T-120, T-121 | `docs/signoff/chg003.md` | 1 | 4 adversarial xanh; invariants kept đã chứng; Confluence Cloud e2e (fixture) xanh; `make ci` xanh không live creds; ADR-0023 proposed→accepted điều kiện (guard + 4 test xanh) đạt về mặt kỹ thuật (promotion do SA/DM) |
| T-123 | BE | Operational gate: live run thật sau env flag — tài liệu bước CEO chạy (không phần `make ci`) | Gate mọi live egress thật sau `MCP_INGEST_ALLOW_LIVE_EGRESS=true` + token thật; `make ci` **không** đụng creds thật (dùng fixture + fake token + model stub). Tài liệu bước vận hành CEO chạy (set token file → `doctor` → `run` live) trong `docs/signoff/chg003.md` §operational, nhắc **không** chạy runbook với token thật trước khi Gate-1 recorded (đã recorded D-006/plan-approval). Không chạy live trong CI. | FR-023/AC-001 (operational), FR-025/AC-001 (ADR-0023 Appendix A header); NFR-014 | T-122 | `docs/signoff/chg003.md` (§operational), `packages/mcp_ingest/tests/test_live_egress_gated_off_in_ci.py` | 1 | CI khẳng định `MCP_INGEST_ALLOW_LIVE_EGRESS` mặc định false ⇒ 0 outbound thật trong test; bước live tài liệu rõ là CEO-run, gated sau env flag + token thật |

## Parallelism — CHG-003

Không FE. Song song giữa task BE độc lập, **sau khi T-111 (egress guard) xanh**:

| Có thể chạy song song | Điều kiện | Ghi chú |
|---|---|---|
| **T-121 (secret-handling) ∥ T-113..T-114 (Confluence enablement)** | T-111 xong | Scrub/`.env.example` dùng `redact.py` nền, không phụ thuộc Confluence Cloud wiring. |
| **T-117 (HF allow-list) ∥ CE2 (T-113..T-116)** | T-111 xong | Model egress tách rời Atlassian (ADR-0023 §6d) — hai luồng độc lập. |
| **T-119 (runbook ingestable) ∥ T-120 (runbook live-only)** | T-116 xong | Hai lớp nguồn độc lập; live-only không phụ thuộc ingest pull. |
| **T-116 (generalise 3 nguồn)** | T-115 xong | GitLab/OpenSearch/Jira độc lập với nhau sau khi khuôn Confluence khoá. |

**Đường tuần tự dài nhất (critical path CHG-003):**
`T-111 (guard + 4 adversarial) → T-112 (wire pull) → T-113 (Confluence Cloud) → T-114 (doctor read-only Cloud) → T-115 (Confluence e2e fixture) → T-116 (generalise) → T-119 (runbook ingestable) → T-122 (sign-off) → T-123 (operational gate)`.
Rút ngắn: chạy **T-121 (secret) ∥ T-113/114**, **T-117/118 (model) ∥ CE2**, **T-120 (live-only runbook) ∥ T-119**.

## Milestones vs baseline — CHG-003

> **Baseline CHG-003:** D-006 §5 ước lượng **build ≈ 1–3 agent-days** (reuse connector/CLI; việc chính =
> ADR + runbook + egress allow-list guard + adversarial test + doctor read-only Cloud). Lấy **mốc baseline
> = 24 agent-h** (3 agent-days × 8h, high-end của D-006). Baseline go-live date **absent** (CHG-003 chưa có
> go-live date đã duyệt ở Gate 1 — Option B duyệt scope, chưa chốt date), nên `schedule_delta_pct` tính so
> **effort baseline 24 agent-h** (D-006), đánh dấu baseline date absent.

| Milestone (epic) | Baseline (D-006, agent-h) | Planned (estimate high-end) | Delta % |
|---|---|---|---|
| CE1 egress guard (T-111,T-112) | *trong 24h bucket D-006* | 3 + 2 = **5 agent-h** | — |
| CE2 Confluence + generalise (T-113..T-116) | *trong 24h bucket* | 2+2+3+3 = **10 agent-h** | — |
| CE3 model download (T-117,T-118) ∥ CE2 | *trong 24h bucket* | max(2+3) với CE2 = **song song, 0 thêm critical** | — |
| CE4 runbook (T-119,T-120) | *trong 24h bucket* | max(2, 2) = **2 agent-h** (T-119∥T-120) | — |
| CE5 secret (T-121) ∥ CE2 | *trong 24h bucket* | **song song, 0 thêm critical** | — |
| Sign-off (T-122,T-123) | *trong 24h bucket* | 1 + 1 = **2 agent-h** | — |

**Số học `schedule_delta_pct`** (dùng high-end estimate, công thức lead):
- Tổng high-end **critical path** (tận dụng T-121∥CE2, T-117/118∥CE2, T-119∥T-120) =
  `5 (CE1) + 10 (CE2) + 2 (CE4) + 2 (sign-off) = 19 agent-h` (CE3 + CE5 chạy trong cửa sổ CE2, không thêm critical).
- Tổng high-end **không song song** = `5 + 10 + 5 (CE3) + 2 (CE4) + 2 (CE5) + 2 = 26 agent-h`.
- `schedule_delta_pct = (planned_high_end − baseline) / baseline × 100 = (19 − 24) / 24 × 100 = −20.8%`
  (dùng critical-path 19h vs baseline D-006 24h). Tức planned **dưới** baseline D-006 ~21% — trong envelope,
  **không** vượt `ESCALATE_SCHEDULE_PCT=20%` theo chiều over-run (delta âm = nhanh hơn baseline).
  Trần không-song-song 26h ⇒ `(26 − 24)/24 × 100 = +8.3%` < 20% — vẫn trong envelope kể cả xấu nhất.
- Baseline go-live **date** absent ⇒ delta theo **date** = n/a; CTO chốt khi set go-live date CHG-003.

## Release path — CHG-003 (dev → UAT → PRE → CAB → PROD → watch)

Giữ `squad-env-promotion` của nền + điều kiện CHG-003:

| Cổng | Phải done trước |
|---|---|
| **dev** | T-111 (egress guard + 4 adversarial §6e) xanh **trước** mọi task wire pull (T-112+); CE1..CE5 unit/integration xanh với **fixture + fake token** trên `make ci` (không live creds). |
| **UAT/PRE** (n/a-shared theo thiết kế — chạy PRE-equivalent host dev + Docker) | 4 adversarial ADR-0023 §6e xanh; Confluence Cloud e2e (fixture) xanh; doctor read-only Cloud chứng; token-never-leaks (forced-error) xanh; 9 server 0 port/0 egress ngoài upstream; **live run thật** (nếu chạy) chỉ sau `MCP_INGEST_ALLOW_LIVE_EGRESS=true` + token thật — bước CEO chạy, ghi evidence vào release-log. |
| **CAB** (Gate 2 CEO MỚI — CHG-001 cab cũ không phủ change này) | ADR-0023 proposed→accepted (guard + 4 test xanh + CEO Gate-1 words recorded — DM owns); allow-list = source host + `huggingface.co` only; token read-only least-privilege, scrub 2 chiều; **NFR-003 vẫn UNVERIFIED** (opening HF egress chỉ *enable* đo — mang caveat lên CEO như D-002 ĐK1); backup-check trước bất kỳ live run. |
| **PROD → watch** | Live egress thật chỉ sau env flag; rollback: set `MCP_EGRESS_ALLOWLIST` rỗng (default-deny lại) + gỡ server khỏi config; token xoay nếu nghi lộ; observation window + SLI như nền; bất kỳ outbound host lạ → alert + rollback. |

## Definition of Done — CHG-003

Kế thừa DoD nền (10) + CHG-001 (11–14) + bổ sung:

15. **Egress default-deny (BR-013/NFR-013):** `MCP_EGRESS_ALLOWLIST` default rỗng ⇒ deny; **một** choke
    point mọi outbound đi qua (grep `check_egress`); 4 adversarial ADR-0023 §6e xanh; 9 server + Jira 0
    outbound ngoài upstream + 0 cổng mạng (regression NFR-005/012).
16. **Read-only-to-source qua ingestion thật (BR-014):** `mcp-ingest` chỉ GET/HEAD tới source, ghi chỉ
    `kb.*` dưới `mcp_ingest_rw`; 9 server + Jira 0 write tool; `doctor` từ chối account write-capable.
17. **Credential never leaked (BR-015/NFR-014):** token env/`*_FILE` only, không commit/log/return;
    scrub cả tool boundary **và** error/log path; forced-error adversarial xanh; `.env.example` placeholders.
18. **Model egress separable + NFR-003 enabled-not-proven (BR-016):** `huggingface.co` allow-list chỉ cho
    download step; `HF_HUB_OFFLINE` flip online chỉ lúc tải; serving offline 0 outbound; ADR-0010 provisional,
    `calibration_status=uncalibrated`, **0** số recall/τ bịa (L-002); fake provider vẫn default/test path.
19. **CI không cần live creds:** `make ci` xanh với fake/stub token + fixture + model stub; live run gated
    sau `MCP_INGEST_ALLOW_LIVE_EGRESS` — bước vận hành CEO chạy.

## Risks & mitigations — CHG-003

| # | Rủi ro | Task xử lý | Giảm thiểu / ghi chú |
|---|---|---|---|
| R-D1 | Egress mở quá rộng / không phải một choke point (phá default-deny) | **T-111**, T-112 | Một module `egress.py` default-deny, mọi pull/model qua `check_egress` (grep structural); 4 adversarial §6e là Done, không prose-only (L-001). |
| R-D2 | Host ngoài allow-list lọt im lặng (unlisted-host leak) | **T-111** (#1/#2) | Default-deny: thiếu config ⇒ deny; adversarial feed host không-list → assert refuse. |
| R-D3 | Token lộ ra log/stdout/result (đặc biệt đường lỗi) | **T-121** | Scrub 2 chiều (ADR-0015, E-003); forced-error adversarial §6e#3; `.env.example` placeholder only. |
| R-D4 | Account token ghi được → mutate source | **T-114** | `credential_check` `_WRITE_OPERATIONS` phủ Cloud; `build_server()`/doctor refuse; escape hatch log WARN. |
| R-D5 | 9 server vô tình egress/mở port khi wire guard (phá NFR-005/012) | **T-111**, T-112, T-122 | Test: server stdio chỉ upstream read API, 0 listening socket; egress thuộc ingest pull + model, không server. |
| R-D6 | Model download kéo serving online (phá no-egress serving) | **T-118** | `HF_HUB_OFFLINE` flip online **chỉ** download step rồi về 1; test serving 0 outbound. |
| R-D7 | Bịa số recall/τ khi có model thật (đọc proxy như proof) | **T-118**, T-122 | ADR-0010 provisional, `calibration_status=uncalibrated`, 0 số bịa (L-002/E-004); đo thật là eval sau (spike S2), ngoài AC CHG-003. |
| R-D8 | Live creds rò vào CI / chạy live ngoài ý muốn | **T-123** | `MCP_INGEST_ALLOW_LIVE_EGRESS` default false; `make ci` fixture + fake token + model stub; live = bước CEO. |
| R-D9 | Live-only source bị ingest nhầm | **T-120** | Registry chỉ {confluence,gitlab,opensearch,jira}; `run --source <live-only>` bị từ chối; runbook never ingest 5 live-only. |
| R-D10 | GitLab/OpenSearch/Jira host chưa chốt vào allow-list (OQ-10) | **T-116** | Allow-list configurable, default-deny host chưa-config; thêm host chỉ khi connector bật — non-blocking, ghi OQ-10. |

## Coverage matrix — CHG-003 AC → task

| FR / AC | Task phủ |
|---|---|
| FR-023/AC-001 | T-113, T-115, T-123 |
| FR-023/AC-002 | T-115 |
| FR-023/AC-003 | T-112, T-115 |
| FR-023/AC-004 | T-116, T-119 |
| FR-024/AC-001 | T-111, T-113 |
| FR-024/AC-002 | T-111, T-117 |
| FR-024/AC-003 | T-111, T-112 |
| FR-025/AC-001 | T-113, T-123 |
| FR-025/AC-002 | T-121 |
| FR-025/AC-003 | T-114 |
| FR-026/AC-001 | T-116, T-119 |
| FR-026/AC-002 | T-120 |
| FR-026/AC-003 | T-120 |
| FR-026/AC-004 | T-120 |
| FR-027/AC-001 | T-118 |
| FR-027/AC-002 | T-117 |
| FR-027/AC-003 | T-118 |

**17 AC id mới (FR-023:4, FR-024:3, FR-025:3, FR-026:4, FR-027:3 = 17) / 17 được phủ. `uncovered_ac` rỗng.**

| NFR (CHG-003) | Task phủ |
|---|---|
| NFR-013 (egress default-deny) | T-111, T-112, T-117, T-120, T-122 |
| NFR-014 (credential never leaked + NFR-003 enabled-not-proven) | T-114, T-118, T-121, T-122, T-123 |

## ADR-0023 §6e adversarial/invariant test → task map (cho QA-plan)

| ADR-0023 §6e test | Intent | Task | FR/AC + NFR |
|---|---|---|---|
| **#1 Egress default-deny proven** | host không-list bị refuse, không im lặng | **T-111** | FR-024/AC-002; NFR-013 |
| **#2 Allow-list honoured** | source host config qua, host chưa-config deny | **T-111** (+T-117 cho HF) | FR-024/AC-001; FR-027/AC-002; NFR-013 |
| **#3 Token never leaks** | forced-error → token vắng log/stdout/result (scrub 2 chiều) | **T-121** | FR-025/AC-002; NFR-014 |
| **#4 Server read-only + stdio unchanged** | 9 server + Jira read-only-to-source + stdio, 0 port; doctor refuse write-capable | **T-111, T-114, T-115, T-120** | FR-023/AC-003, FR-024/AC-003, FR-025/AC-003, FR-026/AC-004; NFR-012/013/006 |

## Epic → task map (CHG-003)

| Epic | Task | Chặn / phụ thuộc |
|---|---|---|
| **CE1** (egress guard) | T-111 (default-deny + 4 adversarial **đầu tiên**), T-112 (wire pull) | **CHẶN trước mọi pull thật CHG-003** (L-001 one choke point) |
| **CE2** (Confluence first + generalise) | T-113, T-114, T-115, T-116 | dep CE1 |
| **CE3** (model download) | T-117, T-118 | dep T-111 — **∥ CE2** (separable, ADR-0023 §6d) |
| **CE4** (runbook) | T-119, T-120 | dep T-116 |
| **CE5** (secret) | T-121 | dep T-113 — **∥ CE2** |
| Sign-off | T-122, T-123 | dep tất cả |

## Ghi chú cho qa-plan (CHG-003)

- **4 adversarial ADR-0023 §6e là test ưu tiên cao nhất (L-001):** #1 default-deny (host không-list refuse),
  #2 allow-list honoured, #3 token-never-leaks (**forced-error** trên credential path, scrub 2 chiều),
  #4 server read-only/stdio unchanged. Map task ở bảng trên. Mỗi cái tại **một** choke point.
- **Egress guard là một structural choke point** — QA test grep/structural: mọi đường pull + model qua
  `check_egress`, không đường vòng (giống GT-5 single-gate của CHG-001).
- **Negative ưu tiên:** FR-024/AC-002 (unlisted host refused), FR-025/AC-002 (forced-error → token vắng
  cả result + log), FR-025/AC-003 (write-capable account refused), FR-023/AC-002 (source unreachable →
  partial, không bịa, checkpoint không advance), FR-027/AC-002 (model egress separable, host khác deny).
- **NFR-003 enabled-not-proven (L-002):** FR-027/AC-003 là **assertion trung thực**, không phải đo chất
  lượng — QA assert `calibration_status=uncalibrated` + **không** có số recall/τ; đo thật (spike S2) là
  eval sau, ngoài scope AC CHG-003.
- **CI không cần live creds:** mọi test dùng fixture + fake token + model stub; `MCP_INGEST_ALLOW_LIVE_EGRESS`
  default false (T-123). Live run thật là bước CEO chạy, QA **không** đưa live creds vào `make ci`.
- **Live test** (nếu chạy against `tnexwm.atlassian.net` thật) gắn `@pytest.mark.live`; unreachable → skip
  và **phải nêu trong regression-report** (như nền), không im lặng bỏ qua.

---

HANDOFF
feature: mcp-data-platform
status: done
artifacts: [implementation-plan.md]
counts: BE=13 FE=0 other=0   # task MỚI CHG-003 (T-111..T-123); nền T-001..T-110 KHÔNG đổi
new_task_range: T-111..T-123 (13 task mới, Owner=BE)
x_change: CHG-003
epic_order: CE1 (egress guard FIRST) → CE2 (Confluence first → generalise) → CE3 (model, ∥ CE2) → CE4 (runbook) → CE5 (secret, ∥ CE2) → sign-off
epic_task_map:
  CE1: [T-111, T-112]   # T-111 egress guard + 4 adversarial = ĐẦU TIÊN, chặn mọi pull thật (L-001)
  CE2: [T-113, T-114, T-115, T-116]   # dep CE1; Confluence Cloud tnexwm.atlassian.net FIRST → generalise GitLab/OpenSearch/Jira
  CE3: [T-117, T-118]   # dep T-111; ∥ CE2 (model egress separable, ADR-0023 §6d)
  CE4: [T-119, T-120]   # dep T-116; T-119 ingestable ∥ T-120 live-only
  CE5: [T-121]          # dep T-113; ∥ CE2 (secret-handling)
  signoff: [T-122, T-123]
adr_0023_adversarial_tests:
  "#1 egress default-deny": T-111 (FR-024/AC-002, NFR-013)
  "#2 allow-list honoured": T-111 + T-117 (FR-024/AC-001, FR-027/AC-002, NFR-013)
  "#3 token never leaks (forced-error, scrub both ways)": T-121 (FR-025/AC-002, NFR-014)
  "#4 server read-only + stdio unchanged": T-111, T-114, T-115, T-120 (FR-023/AC-003, FR-024/AC-003, FR-025/AC-003, FR-026/AC-004; NFR-012/013/006)
critical_path: T-111 → T-112 → T-113 → T-114 → T-115 → T-116 → T-119 → T-122 → T-123
schedule_estimate_agent_h: ~19 (critical path, tận dụng CE3∥CE2 + CE5∥CE2 + T-119∥T-120) / ~26 (không song song)
schedule_delta_pct: -20.8 (critical 19h vs D-006 baseline 24h; +8.3 ở trần không-song-song 26h — cả hai trong envelope, không vượt ESCALATE_SCHEDULE_PCT=20% over-run); baseline go-live DATE absent — CTO chốt khi set go-live date CHG-003
uncovered_ac: []   # 17 AC mới (FR-023:4, FR-024:3, FR-025:3, FR-026:4, FR-027:3) / 17 phủ
schema_change: none   # DK2 (E1 T-087) đã done; CHG-003 không thêm schema. Nếu một task buộc thêm schema → phải theo khuôn DK2 (gọi ra trong Done when)
invariants_kept: read-only-to-source (ingest GET/HEAD source, ghi chỉ kb.* dưới mcp_ingest_rw; 9 server + Jira 0 write), stdio/no-new-port (NFR-005/012; egress thuộc ingest pull + model, không server), default-deny egress (allow-list = source hosts + huggingface.co only), token read-only env/*_FILE only scrub 2 chiều, grounding/permission choke points (CHG-001) không đổi
model_egress_separable: yes (ADR-0023 §6d) — Atlassian ingest chạy khi model deferred; HF_HUB_OFFLINE flip online CHỈ download step
nfr_003: enabled-not-proven (L-002) — mở HF egress CHỈ enable đo; recall + τ_fact/τ_low vẫn TBD/UNVERIFIED (golden-set bake-off spike S2 là eval sau, ngoài AC CHG-003); calibration_status=uncalibrated, 0 số bịa; ADR-0010 giữ provisional
ci_no_live_creds: yes — make ci xanh với fake/stub token + fixture + model stub; live run gated sau MCP_INGEST_ALLOW_LIVE_EGRESS=true (bước vận hành CEO chạy, T-123)
check_sh: scripts/squad/check.sh plan docs/squad/features/mcp-data-platform → PASS (xem kết quả dưới)
next_role: squad-qa mode qa-plan
qa_plan_notes:
  - 4 adversarial §6e là test ưu tiên cao nhất (L-001); map task ở "ADR-0023 §6e → task map".
  - egress guard = một structural choke point (grep check_egress, không đường vòng).
  - FR-027/AC-003 là assertion trung thực (calibration_status=uncalibrated, 0 số recall/τ), KHÔNG đo chất lượng.
  - CI dùng fixture + fake token + model stub; không đưa live creds vào make ci.
