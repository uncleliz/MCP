# mcp-data-platform — Review Report (round 1)

- **Diff scope**: `git diff 0f5c20e..HEAD` — 451 files, ~67.875 dòng thêm, 14 commit (Phase 1 → 3b).
- **Phạm vi**: 11 package Python dưới `packages/`, cộng `e2e/`, `scripts/`, `infra/`, `ci/`, `eval/`, `docs/`.
- **Reviewer đã chạy (song song)**: `ecc:code-reviewer`, `ecc:security-reviewer`, `ecc:python-reviewer`,
  `ecc:database-reviewer`, `ecc:rag-pipeline-reviewer` + kiểm tra riêng của Reviewer
  (contract-conformance, traceability FR/AC/TC, scope).
- **Verdict**: **CHANGES_REQUESTED** — 0 CRITICAL, 5 HIGH, 12 MEDIUM, 10 LOW.

---

## 1. Kiểm tra riêng của Reviewer

### 1.1 Contract conformance — PASS

| Hạng mục | Kết quả |
|---|---|
| Số operation trong `api-contract.yaml` | 55 (49 MCP tool + 6 CLI `mcp-ingest`) |
| Tên tool: contract vs `tools.snapshot.json` của 9 package | **49/49 khớp tuyệt đối**, không thừa/thiếu |
| Input schema (`properties` + `required`) từng tool | **Không có sai lệch nào** trên cả 49 tool |
| `ErrorCode` enum (11 giá trị) | Contract ≡ `mcp_common/errors.py` |
| `ResultStatus` enum (4 giá trị) | Contract ≡ `mcp_common/envelope.py` |
| `scripts/validate_contract.py` | OK |
| `scripts/verify_tool_surface.py` | **42/42 check passed** (read-only surface, prompts, CLI, không rò credential ingest) |
| Response code | Chỉ `200` + `default` — nhất quán toàn contract |

Ba sai lệch backend tự khai báo trong flow, đã xác minh lại:

- `prune` yêu cầu `--older-than` → **vẫn là sai lệch thật**, xem **R-012**.
- `status --json` thiếu failures count → **không phải vi phạm**. `IngestStatusRow` trong contract có
  đúng 7 field (`source_type`, `document_count`, `chunk_count`, `last_run_at`, `last_run_status`,
  `last_success_at`, `staleness_hours`) và `commands/status.py:66-75` trả về đúng 7 field đó. Đóng.
- Đường dẫn runbook → **không phải vi phạm**. `infra/dev-setup.md` và
  `infra/scheduler/runbook-ingest.md` đều tồn tại; `architecture.md:800` đã chốt `infra/`. Phần còn
  lại chỉ là tham chiếu tài liệu chết, xem **R-025**.

### 1.2 Traceability — PASS ở mức AC, có khoảng trống ở mức thực thi

| Hạng mục | Kết quả |
|---|---|
| FR định nghĩa trong `requirements.md` | 15 (FR-001…FR-015), **tất cả Priority = Must** |
| AC định nghĩa | 37 (đánh số lại theo từng FR) |
| AC có ≥1 test mang đúng id | **37/37** — không AC nào thiếu test được gắn id |
| TC trong `test-cases.md` | 77 (TC-072 là tombstone CLOSED) |
| TC được gắn id trong code test | 42/77 |
| FR được tham chiếu trong code test | 15/15 |

Hai nhận xét:

- 35 TC không mang id trong code test. Phần lớn **vẫn được phủ hành vi** (ví dụ TC-001/003 nằm trong
  `packages/mcp_confluence/tests/test_read_api.py` nhưng gắn id theo `FR-001/AC-001` thay vì `TC-001`).
  Vì tiêu chí bắt buộc là "mỗi AC có ≥1 test mang id" và tiêu chí đó **đạt 37/37**, đây chỉ là LOW
  (**R-026**), không phải lỗ hổng phủ.
- FR-002, FR-005, FR-006, FR-007, FR-008, FR-010 **không có TC ở mức E2E** (chỉ Integration). TC-056
  phủ read-only surface của cả 9 server qua stdio thật, nhưng đường happy-path của 6 FR này chưa bao
  giờ đi qua transport MCP stdio thật. Đây là quyết định có chủ ý ở stage `qa-plan`, Reviewer không
  mở lại — ghi nhận ở LOW (**R-026**).

### 1.3 Scope — PASS

- Không có file nào nằm ngoài `packages/`, `infra/`, `scripts/`, `ci/`, `docs/`, `eval/`, `e2e/` và
  config gốc (`pyproject.toml`, `ruff.toml`, `uv.lock`, `Makefile`, `.python-version`, `.gitignore`,
  `.env.example`, `CLAUDE.md`). Không có `backend/`/`frontend/` lạc chỗ.
- **Không có secret thật nào được commit.** Grep toàn diff theo pattern AWS/GitLab/GitHub/JWT/PEM chỉ
  trúng fixture test và vector kiểm thử redaction (`AKIAIOSFODNN7EXAMPLE` — chính là khóa ví dụ công
  khai của AWS, `glpat-not-a-real-token-*`, `changeme-*` trong `.env.example`). `.gitignore` chặn
  `.env`/`*.env` và giữ `!.env.example`. Đúng.
- `.claude/settings.json` mới chỉ thêm `GATEGUARD_EXEMPT_GLOBS=docs/squad/**` — tooling squad, không
  nới quyền. Riêng `.claude/squad/backup/**` là rác backup bị commit, xem **R-027**.

### 1.4 Tái lập kết quả test tại HEAD — PASS

`uv run pytest packages/ e2e/ -q` trên máy review: **1846 passed, 174 skipped** — trùng khớp
`regression-report.md` (1822 BE + 24 E2E = 1846 passed; 164 + 10 = 174 skipped). 0 failed, 0 error.
Con số trong regression report là đúng và tái lập được. **Nhưng** xem **R-004** và **R-005** về việc
174 skip đó che mất gì.

---

## 2. Bảng findings

| ID | Severity | Area | File:line | Finding | Failure scenario | Reviewer | Owner |
|---|---|---|---|---|---|---|---|
| **R-001** | **HIGH** | BE | `packages/mcp_gitlab/src/mcp_gitlab/client.py:205-211`, `read_api.py:794-839`; `packages/mcp_common/src/mcp_common/errors.py:48` | `get_text()` trả `response.text` — không `stream=True`, không kiểm `Content-Length`, không trần byte. `get_job_trace()` tải **toàn bộ** trace vào RAM rồi mới `_tail_bytes()` cắt theo `max_bytes`. Tối đa 3 trace/lần gọi. Mã lỗi `response_too_large` được khai báo trong cả contract và `ErrorCode` nhưng **không nơi nào raise, không test nào phủ** | Client MCP gọi `gitlab_get_pipeline(include_failed_job_trace=true)` lên pipeline có job log vài trăm MB (bình thường với CI nói nhiều). httpx buffer toàn bộ body, rồi `scrub()` quét regex + entropy trên toàn bộ text chưa cắt → đỉnh RAM/CPU, OOM kill tiến trình `mcp_gitlab`, mất cả session MCP của user. Không cần quyền đặc biệt, `max_bytes` (1024..131072) chỉ chặn phần trả cho model, không chặn phần tải về. Deadline tool 25s là biên duy nhất | security-reviewer, Reviewer | backend |
| **R-002** | **HIGH** | BE | `packages/mcp_gitlab/src/mcp_gitlab/settings.py:14`; `packages/mcp_ingest/src/mcp_ingest/pipeline/redact.py:17,53-61` | `DEFAULT_PATH_DENY = "*.env,*secret*,*credential*,*.pem,id_rsa*"` quá hẹp. Đã kiểm bằng `fnmatch` thực tế: **ALLOWED** cho `.env.local`, `.env.production`, `config/.env.staging`, `id_ed25519`, `certs/server.key`, `sa.p12`, `truststore.jks`, `.npmrc`, `.netrc`, `terraform.tfstate`. `*.env` chỉ khớp tên **kết thúc** bằng `.env` | Repo có `config/.env.production` chứa `DB_PASS=<giá trị thật>`. Path qua được deny-glob; nếu giá trị ngắn/entropy thấp và tên key không thuộc PASSWORD/SECRET/TOKEN/API_KEY/PRIVATE_KEY thì cũng qua được `scrub()`. Bất kỳ caller có `gitlab_get_file`/`gitlab_search_code` nhận secret nguyên văn — và vì `mcp_ingest` dùng **đúng cùng danh sách**, secret còn được ghi bền vào `kb.chunks`. Giảm nhẹ: mọi *hình dạng* token quen biết vẫn bị `scrub()` bắt, nên rủi ro còn lại là secret dạng thuần/nhãn lạ | security-reviewer, Reviewer | backend |
| **R-003** | **HIGH** | BE | `packages/mcp_common/src/mcp_common/errors.py:95-108` (`to_error_envelope`), `:198-248` (`_NON_HTTPX_SDK_RULES`), `:274`; `tooling.py:362-368`; `runtime.py:112-117`; `logging.py:53-72` | Đường lỗi **không đi qua `scrub()`**. `to_error_envelope()` serialize `error.message`/`error.details` thẳng vào `CallToolResult`. Ba rule nội suy raw `str(exc)`: `f"boto3 ClientError: {exc}"`, `f"Postgres operational error: {exc}"`, `f"Redis response error: {exc}"`. `JSONStderrFormatter.format()` cũng không scrub. Docstring `redact.py:3-9` khẳng định scrub áp cho "(a) mọi log record và (b) mọi free-text trước khi trả trong tool result" — **cả hai đều không đúng cho đường lỗi** | Reachable từ 6 package (`mcp_cloudwatch/client.py:146`, `mcp_redis/client.py:241,264`, `mcp_sqs_sns/client.py:159`, `mcp_kafka/client.py:185`, `mcp_opensearch/client.py:218`, fallback `mcp_pgvector/client.py:150`). `mcp_pgvector` có test riêng chứng minh không rò DSN (`test_db_integration.py:389`) nhưng fallback generic này **đi vòng qua** chính bảo đảm đó: một `psycopg.OperationalError` ("connection to server at ... failed: FATAL: ...") trả nguyên văn cho client. Hôm nay chưa chắc rò credential, nhưng lưới an toàn duy nhất kiến trúc hứa thì vắng mặt — mất hiệu lực ngay khi SDK đổi nội dung message hoặc thêm rule mới | python-reviewer (HIGH), security-reviewer (MEDIUM), Reviewer | backend |
| **R-004** | **HIGH** | tests/contract | `scripts/recall_benchmark.py:40,51-76,275-278`; `docs/signoff/phase-3.md:27`; `test-cases.md` TC-040/TC-066; ADR-0011 A3 | Con số "recall ≥ 0.95" **không phải bằng chứng về chất lượng truy hồi**. `synthetic_documents()` và `synthetic_queries()` sinh corpus *và* truy vấn từ **cùng một generator seed** (ground truth = token `topicNwM` dùng chung) ⇒ leakage hoàn toàn. `main()` mặc định `DeterministicFakeProvider` (bag-of-words hash). Cái được đo là HNSW có trả cùng row với brute-force (`enable_indexscan=off`) — tức **ANN index correctness**, không phải semantic relevance; nó vẫn ra ~1.0 dù embedding model là noise | `signoff/phase-3.md:27` ghi "**Recall NFR-003 ≥ 0.95**: … mean 0.994, min 0.90" trong bảng evidence, và flow coi ADR-0011 A3 là gate đã xanh. Gate C sẽ được duyệt với niềm tin NFR-003 đã được chứng minh, trong khi chưa có phép đo nào nói lên truy hồi có tìm đúng chunk cho câu hỏi thật. Giảm nhẹ (lý do không phải CRITICAL): docstring script (`:14-16`) và `signoff/phase-3.md:41` + checklist `:68` **chưa tick** đã tự ghi nhận hạn chế này | rag-pipeline-reviewer (CRITICAL), database-reviewer (LOW), Reviewer | sa (chỉnh claim ADR-0011/NFR-003) + qa (sửa diễn đạt report) |
| **R-005** | **HIGH** | tests | `e2e/test_pgvector_ingest_e2e.py` (toàn file, 363 dòng, thêm ở commit `0363fcc`); `packages/conftest.py:61-63` | 10 test E2E — TC-045, 049, 051, 052, 053, 067, 068, 073, 074, 077, **tất cả P1** — **chưa từng chạy ở bất kỳ đâu**. Toàn bộ `e2e/` được tạo ở commit HEAD `0363fcc` ("WIP e2e tests from interrupted qa-verify"), tức **sau** lần chạy xanh Linux cuối (`2b4bbda`, 1975 passed). Lần chạy duy nhất của chúng (qa-verify) skip 100%. Các test integration tương đương cũng skip cùng lý do. Phụ: `_find_pg_bin()` hardcode `/usr/lib/postgresql/*/bin/initdb` (chỉ Debian) — không đọc `PATH`, không có env override, nên skip trên mọi host không-Debian | FR-013/AC-001 và FR-013/AC-002 **không có test hành vi nào đã thực thi ở bất kỳ mức nào**: `test_prompts.py` chỉ assert *câu chữ của prompt*, `test_eval_phase3.py` chỉ assert *shape của YAML*. Journey 3 (FR-013, Must) chưa được kiểm chứng. Code test chưa từng chạy thì xác suất cao bản thân nó lỗi, nên "chạy lại trên máy Linux" không phải thủ tục mà là rủi ro thật. Reviewer đã thử chạy tại chỗ: `initdb` có ở `/Library/PostgreSQL/17/bin` nhưng **pgvector không được cài** (`vector.control` không tồn tại) ⇒ không thể gỡ bế tắc trên host này | Reviewer | qa |
| R-006 | MEDIUM | BE | `packages/mcp_ingest/migrations/0006_review_followup.sql:8-17` | `ADD CONSTRAINT documents_visibility_ck CHECK (...)` không có `NOT VALID` ⇒ Postgres validate bằng full scan trong lúc giữ `ACCESS EXCLUSIVE` trên `kb.documents`, và cả file nằm trong một transaction (`db.py:128`) nên không chen vào được | `kb_semantic_search`/`kb_get_document` treo hoặc timeout suốt thời gian migrate. Hạ từ HIGH xuống MEDIUM vì **cả 6 migration cùng về trong một commit (`fb53599`)** nên chưa tồn tại môi trường nào đang ở 0005 với dữ liệu thật — latent, không live | database-reviewer (HIGH→MEDIUM) | backend |
| R-007 | MEDIUM | BE | `packages/mcp_ingest/src/mcp_ingest/db.py:128`; `migrations/0003_indexes.sql:4-8` | Runner bọc mỗi file migration trong `with conn.transaction():` ⇒ `CREATE INDEX CONCURRENTLY` **không thể** dùng (Postgres cấm trong transaction block), triệt tiêu đúng biện pháp mà comment `0003_indexes.sql:2-3` viện dẫn | Index tiếp theo thêm lên bảng đã có dữ liệu (ví dụ index `tsvector` cho hybrid search ở ADR-0011 "Negative") sẽ khóa ghi toàn bảng. Hạ từ HIGH xuống MEDIUM: index hiện tại đều tạo trên bảng rỗng — latent | database-reviewer (HIGH→MEDIUM) | backend |
| R-008 | MEDIUM | BE | `packages/mcp_pgvector/src/mcp_pgvector/sql.py:82-90` (gọi từ `read_api.py:200`), `sql.py:92-101` | Thiếu 2 index: (a) subquery freshness `WHERE deleted_at IS NULL GROUP BY source_type` chạy trên **mọi** `kb_semantic_search` mà không có index phủ; (b) `kb.documents.source_uri` không có index nào ⇒ `kb_get_document(source_uri=...)` seq-scan | Khi `kb.documents` lớn, mọi lần search trả thêm một seq/bitmap scan toàn bảng trên đầu truy vấn HNSW; `kb_get_document` theo `source_uri` (input được contract hỗ trợ) tuyến tính theo kích thước corpus. Fix: `(source_type, ingested_at) WHERE deleted_at IS NULL` và `(source_uri)` | database-reviewer | backend |
| R-009 | MEDIUM | BE | `packages/mcp_ingest/src/mcp_ingest/pipeline/persist.py:111-119`; `commands/reembed.py:78-86` | Vòng lặp `INSERT`/`UPDATE` từng dòng thay vì ghi theo batch | N round-trip mỗi document/batch. Ở quy mô "chục–trăm nghìn chunk" (ADR-0011) một lần crawl đầy đủ trả giá latency mạng rất lớn một cách không cần thiết. Correctness vẫn đúng | database-reviewer | backend |
| R-010 | MEDIUM | BE | `packages/mcp_ingest/migrations/0005_roles.sql:16-17`; `db.py:77`; `pipeline/run.py:341` | Chỉ `mcp_query_ro` được `SET statement_timeout = '15s'`. `mcp_ingest_rw` (pipeline, `prune`, `reembed`) không có, cũng không set lúc connect | Một query treo (`DELETE ... WHERE source_type = ANY(%s)` trên mảng ids lớn ở `reconcile.py:60`, hoặc lock-wait của `prune`) giữ lock vô hạn, không có gì kill nó | database-reviewer | backend |
| R-011 | MEDIUM | contract/BE | `packages/mcp_ingest/migrations/0002_schema_kb.sql:4-5,31`; ADR-0010 | `vector(1024)` hardcode trong khi ADR-0010 còn ở trạng thái *proposed* (bge-m3 chỉ là PROVISIONAL, bake-off chưa đo vì egress HF bị chặn). Cả hai ứng viên S2 đều 1024d nên hiện an toàn, nhưng không có runbook cho model khác chiều | Nếu model cuối cùng là 768/1536/3072d: cần `ALTER COLUMN embedding TYPE vector(N)` (rewrite toàn bảng) + drop/rebuild HNSW + full `reembed`, trong khi `kb_semantic_search` không khả dụng hoặc trả kết quả không nhất quán. `reembed.py:46-51` đã guard mismatch nhưng chỉ sau khi migration mới tồn tại | database-reviewer, rag-pipeline-reviewer | sa |
| R-012 | MEDIUM | contract | `api-contract.yaml` `/cli/mcp-ingest/prune`; `packages/mcp_ingest/src/mcp_ingest/commands/prune.py:29-37` + `cli.py` | Contract khai `anyOf: [{required:[tombstoned]}, {required:[older_than_days]}]` — **một trong hai** là đủ. Implementation làm `--older-than` **REQUIRED vô điều kiện** (`--help`: "REQUIRED, e.g. 30d") | Operator/script làm theo contract chạy `mcp-ingest prune --tombstoned` → bị CLI từ chối dù contract cho phép. Hướng lệch là an toàn hơn (không có rủi ro mất dữ liệu) và TC-074 step 1 vẫn đạt, nên MEDIUM. Nhưng theo hard rule của CLAUDE.md, contract là single source of truth: phải nới implementation **hoặc** SA sửa contract thành `required: [older_than_days]` — không được để lệch | Reviewer | contract (sa) |
| R-013 | MEDIUM | BE/infra | `infra/redis/users.acl:2`; `packages/mcp_redis/src/mcp_redis/client.py:50-73` | ACL `mcp_ro` cấp `+hgetall +smembers +mget +hmget` — đúng những lệnh mà `ALLOWED_COMMANDS` **cố tình loại** (read không giới hạn kích thước). Cùng file: `user default on nopass ~* &* +@all` trên port publish `6379:6379` | Chưa reachable qua tool nào (mọi lệnh qua `enforce()` ở `client.py:247-253` trước khi I/O) nên MEDIUM, không HIGH — vi phạm least-privilege, sẽ thành lỗ hổng ngay khi có code path khác hoặc tool khác dùng cùng ACL user. `default nopass +@all` chấp nhận được cho compose dev/test nhưng **tuyệt đối không được tái dùng cho staging/shared** | security-reviewer | backend |
| R-014 | MEDIUM | BE | `packages/mcp_pgvector/src/mcp_pgvector/read_api.py:185-199`; `sql.py:56-69`; `docs/spikes/S3-hybrid-search.md` | Không rerank, không fallback lexical. Kết quả là thứ tự cosine ANN thô, chỉ lọc bằng ngưỡng điểm | "error code `ERR_PAY_4021` nghĩa là gì" — chuỗi ngắn, ít nội dung ngữ nghĩa — có thể xếp chunk đúng dưới một chunk chỉ bàn chung về lỗi, và không có gì re-score hay fallback lexical trong chính `kb_semantic_search`. S3 đã nhận diện đúng lớp truy vấn này nhưng ghi "QUY TRÌNH ĐÃ VIẾT, ĐO ĐẠC CHƯA LÀM" và lùi sau khi pipeline ship | rag-pipeline-reviewer | sa + po (quyết định scope) |
| R-015 | MEDIUM | tests | `scripts/run_eval.py:11-12,108-121`; `packages/mcp_pgvector/tests/test_eval_phase3.py` | Không có harness đo relevance/faithfulness (RAGAS hoặc precision/recall@k trên nhãn tay). `judge()` và `test_eval_phase3.py` chỉ assert status/số citation và shape của question set | Một thay đổi chunking/model/ranking về sau trả chunk nghe hợp lý nhưng sai; `make ci` vẫn xanh vì không gì đo relevance ⇒ regression ship êm. Hạ từ HIGH xuống MEDIUM: TC-065 **cố ý** là Manual với `# THRESHOLD TBD (Open question 1 — PO chốt sau Phase 1)`, tức đây là deferral đã khai báo trong plan, không phải implementation miss — Reviewer không mở lại quyết định của plan | rag-pipeline-reviewer (HIGH→MEDIUM) | po + qa |
| R-016 | MEDIUM | BE/infra | `ci/pipeline.yml:1-9`; không có `.github/workflows/` | CI chỉ là tài liệu. Header của chính file thừa nhận "no CI runner is wired up in this repo yet". Không có pre-commit hook | lint / typecheck / coverage / `validate_contract` / readonly-suite chỉ chạy khi có người gõ `make ci`. Không gì chặn merge bỏ qua cả 5 check. Đáng kể vì sign-off Gate C có thể đang **giả định** CI cưỡng chế các gate đó | python-reviewer | backend |
| R-017 | MEDIUM | BE | `pyproject.toml:33-37` | `[tool.mypy]` chỉ set `python_version`, `files`, `ignore_missing_imports`, `exclude`. Không có `disallow_untyped_defs`, `warn_return_any`, `no_implicit_optional`, `strict` | `uv run mypy` → "Success: no issues found in 140 source files" là tín hiệu **yếu hơn vẻ ngoài rất nhiều**: hàm không annotate và `Any` rò rỉ hiện qua im lặng. (`uv run ruff check .` sạch, `ruff.toml` `ignore = []` — phần ruff thì tốt) | python-reviewer | backend |
| R-018 | LOW | BE | `packages/mcp_common/src/mcp_common/runtime.py:80-121` | `with_tool_deadline` là dead code. Cả 9 server đăng ký tool qua `tooling.py:384` `register_tool`, hàm này tự cài `asyncio.timeout` + error-envelope riêng. Docstring module vẫn khẳng định đây là "the decorator each tool function is wrapped in before being registered" — **sai** | Không phải bug hôm nay (`register_tool` lặp lại đúng hành vi), nhưng sửa một đường deadline sẽ lặng lẽ lệch khỏi đường kia | code-reviewer | backend |
| R-019 | LOW | BE | `packages/mcp_ingest/src/mcp_ingest/pipeline/failures.py:24-32` | Policy block gọi `upsert_failure(attempts=0)` nhưng nhánh INSERT tính `max(attempts,1) if attempts else 1` ⇒ dòng bị block lần đầu vẫn lưu `attempts=1` | Chỉ ảnh hưởng counter chẩn đoán. Comment ("policy blocks pass 0 … so repeated runs do not inflate the counter") chỉ đúng từ dòng thứ hai trở đi | code-reviewer | backend |
| R-020 | LOW | BE | `packages/mcp_ingest/migrations/0005_roles.sql` | Có `REVOKE ALL ON SCHEMA kb FROM PUBLIC` (tốt) nhưng thiếu `REVOKE CREATE ON SCHEMA public FROM PUBLIC` và thiếu `ALTER DEFAULT PRIVILEGES IN SCHEMA kb ...` | Mọi migration tương lai thêm table/sequence phải tự nhớ `GRANT` (như 0006 đã làm tay ở `:39-40`). Quên thì fail-safe (lỗi, không rò) nhưng là lỗi availability dễ mắc | database-reviewer | backend |
| R-021 | LOW | BE | `packages/mcp_common/src/mcp_common/runtime.py:221` (`serve`); ví dụ `mcp_confluence/server.py:41-53` vs `mcp_pgvector/cli.py:40-84` | `aclose()` tồn tại ở cả 9 package nhưng `runtime.serve()` không có `finally`/signal handler gọi nó. Chỉ `mcp_pgvector` làm (`finally: await client.aclose()`) | Vô hại với tiến trình stdio dài hạn (OS thu hồi socket khi exit), nhưng graceful shutdown/draining thực tế chưa được nối, và pattern không nhất quán giữa 9 package | python-reviewer | backend |
| R-022 | LOW | BE | `scripts/recall_benchmark.py:134,154,166-197` | `measure()` là `async def` nhưng gọi `brute_force_ids`/`index_is_used` dùng `psycopg.Connection.execute` đồng bộ (blocking) | Chặn event loop trong lúc `await api.semantic_search(...)` lẽ ra chạy song song. Là CLI benchmark một lần, không phải đường production, nên chỉ là lệch khỏi kỷ luật sync/async mà phần còn lại của repo giữ rất chặt | python-reviewer | backend |
| R-023 | LOW | BE/infra | `packages/mcp_common/src/mcp_common/config.py:78`; `infra/scheduler/run-ingest.sh:26-32` | Convention `*_FILE` đọc `Path(file_path).read_text()` không `os.stat` kiểm mode. `run-ingest.sh` source `$MCP_INGEST_ENV_FILE` (chứa DSN + credential nguồn) cũng không kiểm | `runbook-ingest.md:39` có hướng dẫn `chmod 600` nhưng chỉ là lời khuyên cho operator, không được cưỡng chế. File secret group/world-readable vẫn được nạp im lặng | security-reviewer | backend |
| R-024 | LOW | BE | `packages/mcp_ingest/src/mcp_ingest/pipeline/chunk.py:22,85`; `settings.py:20` | Nhận diện heading ATX áp cho mọi text đã ingest; chỉ trường hợp fenced code block được loại và test (`test_chunker.py:43-47`) | Dòng bắt đầu `#` không fenced trong `.rst`/`.adoc`/`.txt` (đều nằm trong default `MCP_INGEST_GITLAB_FILE_GLOBS`) bị nhận nhầm là heading ⇒ `heading_path` trong citation sai và biên chunk lệch | rag-pipeline-reviewer | backend |
| R-025 | LOW | docs | `architecture.md`, `implementation-plan.md` | 3 tham chiếu tài liệu chết: `docs/dev-setup.md`, `docs/runbook-ingest.md`, `docs/signoff/phase-N.md` (file thật: `infra/dev-setup.md`, `infra/scheduler/runbook-ingest.md`, `docs/signoff/phase-{1,2,3}.md`) | Người đọc plan/architecture đi theo đường dẫn không tồn tại. `architecture.md:800` đã chốt `infra/` nên đây chỉ là tham chiếu chưa cập nhật | Reviewer | sa |
| R-026 | LOW | tests | `test-cases.md` vs code test | (a) 35/77 TC không mang id trong code test (nhưng **37/37 AC đều có test mang id**, nên không có AC nào mất phủ); (b) FR-002/005/006/007/008/010 không có TC mức E2E | Khi một test fail, truy ngược về TC tốn công hơn cần thiết. Happy-path của 6 FR trên chưa bao giờ đi qua transport MCP stdio thật (TC-056 chỉ phủ read-only surface). Đây là quyết định có chủ ý ở `qa-plan`, ghi nhận chứ không mở lại | Reviewer | qa |
| R-027 | LOW | scope | `.claude/squad/backup/20261001-000705/**`, `.claude/squad/backup/manual-v0/CLAUDE.md` | Thư mục backup của squad-init bị commit vào repo | Rác repo; bản sao agent/skill cũ sẽ phân kỳ khỏi bản thật và gây nhầm lẫn khi đọc. Nên vào `.gitignore` | Reviewer | backend |

---

## 3. Những điều đã xác nhận đúng (không cần hành động)

Ghi lại vì chúng là phần cốt lõi của NFR-001 và tốn công mới xác minh được:

- **Read-only enforcement xếp lớp và hiệu quả — không tìm thấy đường mutate nào reachable.**
  - OpenSearch DSL: `ReadOnlyTransport`/`_TRANSPORT_ALLOWLIST` (`client.py:78-129`) chỉ khớp
    `GET /`, `_cat/indices`, `_mapping`, `POST|GET _search`, `POST|GET _count`, `GET authinfo`;
    `_update_by_query`/`_delete_by_query`/`_scripts`/`_ingest/pipeline` không bao giờ khớp fnmatch bất
    kể body. Lớp thứ hai `assert_body_allowed` (`:147-169`) duyệt body ở mọi độ sâu, chặn
    `script`/`runtime_mappings`/`scroll`/`pit`/terms-lookup ngay trong `_search` đã được phép.
  - pgvector: **không tồn tại tool nhận SQL text**. Chỉ một dict `STATEMENTS` đóng, đặt tên, tham số
    hóa toàn bộ; mọi query trong `BEGIN READ ONLY` (`client.py:183-193`); startup check
    (`:259-296`) từ chối role superuser/ghi được. Bề mặt SQL injection: không có.
  - Redis: allowlist cứng cưỡng chế trước **mọi** command kể cả trong pipeline (`client.py:247-265`).
  - Kafka: không có `Producer` ở đâu; consumer không bao giờ commit
    (`enable.auto.commit=False`, `enable.auto.offset.store=False`), dùng `assign()` với `group.id`
    dùng-một-lần thay vì `subscribe()`, `allow.auto.create.topics=False` ở cả admin và consumer.
  - SQS/SNS + CloudWatch: allowlist `service:Operation` cưỡng chế cả lúc gọi **và** qua botocore hook
    `before-parameter-build` (phòng cả trường hợp gọi boto trực tiếp); `sqs:ReceiveMessage` bị loại
    có chủ ý vì mutate visibility timeout.
- **SSRF: không có.** Không tool parameter nào ở bất kỳ package nào cho caller override
  `base_url`/host — `base_url` là server-config, không bao giờ là tool argument.
- **TLS: không có `verify=False`/`verify_certs=False`/`CERT_NONE`** ở bất kỳ `packages/*/src`.
- **Timeout budget** nhất quán và đều dưới deadline tool ngoài: HTTP `connect=3/read=7` + 1 retry
  (21s < 25s), boto3 `connect=3/read=7` + `total_max_attempts=2`, Redis `2s/5s`, Kafka `8s`,
  pgvector `statement_timeout=15s`.
- **HNSW operator khớp query operator**: index `vector_cosine_ops` (`0003_indexes.sql:5`), query
  `ORDER BY embedding <=> %(query)s::vector` (`sql.py:67,78`) — cosine cả hai phía, index thực sự được
  dùng, không có silent-disable. Mọi `ON CONFLICT` đều có unique constraint/PK thật đứng sau
  (`persist.py:93`, `checkpoint.py:113`, `failures.py:27`) nên không clause nào fail lúc runtime.
- **Chuẩn hóa vector nhất quán giữa ingest và query**: `normalize=True`, L2 giống nhau ở cả
  `local.py` và `http.py` qua `_vectors.py:13`.
- **Redaction có áp trước khi ghi bền**: `pipeline/redact.py` chạy trước persist, nên `kb.chunks`
  không lưu secret thô — tách biệt với `scrub()` ở biên tool output, đúng thứ tự ADR-0015 yêu cầu.
- **`uv run ruff check .` sạch**, `ruff.toml` `ignore = []`, có ban `T20` (`print`) — đúng cho
  transport stdio. Chất lượng test mẫu tốt: assert hành vi qua `respx` call-count và biên thời gian,
  không phải tautology mock; không tìm thấy `assert True`/assertion vô hiệu nào.
- **Pydantic v2 dùng nhất quán** ở cả 10 `settings.py`; không trộn API v1/v2. Không có mutable default
  argument, không có closure-over-loop-var, không có `except: pass`.
- Trong lúc review, `ecc:security-reviewer` báo đã **phát hiện và bỏ qua** một khối instruction lạ
  được inject vào context (hướng dẫn tạo document của một MCP server không liên quan). Hành vi đúng;
  không file nào bị ghi bởi reviewer. Ghi lại để phục vụ audit.

---

## 4. Previously reported — fixed / still open

Round 1: không có `review-report.md` trước đó. Dưới đây là các mục mà SA/backend/QA đã nêu trong flow
và trạng thái sau khi Reviewer kiểm lại:

| Mục đã biết | Trạng thái sau review |
|---|---|
| T-001 S1 reachability table + T-015 `docker compose up` cần máy có VPN/Docker | **Vẫn mở, không phải review finding.** Xác minh: host review không có Docker daemon (`docker info` fail). Là infra verification, không phải code defect — thuộc Gate C checklist |
| Backend deviation: `prune` yêu cầu `--older-than` | **Thành finding R-012 (MEDIUM).** "Nghiêm hơn có chủ ý" vẫn là lệch contract, phải đóng ở một trong hai phía |
| Backend deviation: `status --json` thiếu failures count | **Đóng, không phải finding.** Contract không có field đó; `IngestStatusRow` 7/7 field khớp |
| Backend deviation: runbook ở `infra/scheduler/runbook-ingest.md` | **Đóng.** `architecture.md:800` đã chốt `infra/`. Phần tham chiếu chết còn lại → R-025 (LOW) |
| ADR-0011 recall ≥ 0.95 chưa chạy lại trên host macOS của qa-verify | **Nghiêm trọng hơn báo cáo ban đầu → R-004 (HIGH).** Vấn đề không phải "chưa chạy lại trên host này" mà là **phép đo không đo đúng thứ nó tuyên bố**, kể cả trên lần chạy Linux đã xanh |
| Nhánh contract pgvector chưa chạy lại trên macOS | **→ R-005 (HIGH).** Và nặng hơn: 10 test E2E Postgres sinh ra ở HEAD nên **chưa từng chạy ở đâu**, không chỉ là "chưa chạy lại" |
| GitLab job-trace không có size cap (backend nêu ở Phase 1 là "risk for review") | **Xác nhận → R-001 (HIGH).** Reviewer phán quyết: reachable bằng một tool call thường, có thể OOM tiến trình; `response_too_large` đã khai trong contract mà chưa hiện thực |
| Confluence/GitLab deny-glob `*secret*` quá rộng (backend nêu ở Phase 1) | **Xác nhận một nửa, và lo ngại bị đảo chiều → R-002 (HIGH).** `*secret*`/`*credential*` rộng thật nhưng chỉ gây false denial (LOW, usability). Vấn đề thật là glob **quá hẹp**: `.env.local`/`.env.production`/`id_ed25519`/`*.key`/`*.p12`/`.npmrc`/`*.tfstate` đều lọt (đã kiểm bằng fnmatch thực tế) |

---

## 5. Verdict

**CHANGES_REQUESTED**

0 CRITICAL, **5 HIGH**, 12 MEDIUM, 10 LOW. Năm HIGH đang mở nên không đạt điều kiện APPROVE.

Chúng chia thành hai nhóm khác nhau về bản chất và nên route khác nhau:

- **Lỗi code, sửa được ngay — owner `backend`**: R-001 (trần byte cho job trace + hiện thực
  `response_too_large`), R-002 (mở rộng deny-glob), R-003 (scrub đường lỗi và log). Cả ba đều là sửa
  nhỏ, khoanh vùng rõ, kèm regression test.
- **Lỗ hổng bằng chứng xác minh — owner `qa` + `sa`**: R-004 (claim recall ≥ 0.95 không đo đúng thứ nó
  tuyên bố) và R-005 (10 test E2E P1 chưa từng chạy). R-004 **không** sửa được bằng cách đo lại — phép
  đo thật bị chặn sau quyết định model của ADR-0010, vốn bị chặn bởi egress. Hành động đúng cho R-004
  là **sửa claim**: hạ diễn đạt của ADR-0011 A3 / `signoff/phase-3.md:27` / `regression-report.md`
  thành "ANN index recall (synthetic, fake provider)" và ghi NFR-003 là **chưa xác minh**, để Gate C
  được duyệt với thông tin đúng thay vì niềm tin sai.

Lưu ý cho orchestrator: R-005 không thể đóng trên host review hiện tại (có `initdb` ở
`/Library/PostgreSQL/17/bin` nhưng **không có pgvector**, `vector.control` không tồn tại), nên nó cần
một máy Linux/Docker — cùng máy sẽ giải quyết luôn T-001/T-015 và cho phép chạy `--provider configured`
nếu ADR-0010 được chốt. Gộp cả ba vào một lần chạy trên môi trường có Docker là đường ngắn nhất.

Không nên commit (Gate C) trước khi ít nhất 3 HIGH nhóm code được sửa và 2 HIGH nhóm bằng chứng được
đóng hoặc được PO chấp nhận rủi ro một cách tường minh.

---

# mcp-data-platform — Review Report (round 2)

- **Diff scope since round 1**: the five blocking fixes (R-001..R-003 backend code + regression
  tests; R-004 SA/QA claim relabel; R-005 QA first-ever execution of the 10 P1 pgvector E2E tests).
  Branch `claude/zealous-johnson-yb3t2q`, local macOS, Docker UP (pgvector 0.8.6 throw-away container).
- **What round 2 did**: re-verified each blocking item against the code/doc on disk and re-ran the
  relevant checks (`validate_contract`, `verify_tool_surface`, the named R-fix regression tests,
  ruff, the three changed packages' full suites), read `6-verify/regression-report-dev.md` and the
  `evidence/qa-dev/*` logs for R-005, read `records/errors.md`. **No code or state.json was edited**;
  the reviewer only appended the four `### E-… · verified` records to the error ledger.
- **Recurring-pattern scan** (`scripts/squad/errors.sh summary --role squad-reviewer`): the three
  open items were exactly E-001/002/003 (all `security`, all introduced `backend`, all escaped
  `backend`+`qa-plan`+`qa-verify`); no cross-feature RECURRING pattern — this is the feature's first
  error ledger. The common root cause (a guarantee asserted in a docstring but not enforced at the
  single structural choke point, with no test feeding the adversarial input) recurs across all three
  and is worth a lesson in retro.

## 1. Blocking items — verification

| ID | Severity | Owner | Verified fix | Evidence re-checked by reviewer | Status |
|---|---|---|---|---|---|
| **R-001** | HIGH | backend | `get_text()` streams (`http.stream`), rejects on declared `Content-Length > cap` **and** on cumulative `aiter_bytes()` total `> cap`, raising `ErrorCode.RESPONSE_TOO_LARGE`; `get_job_trace()` passes `max_bytes=max_job_trace_bytes`; new setting `MCP_GITLAB_MAX_JOB_TRACE_BYTES` (10 MiB) independent of `MCP_MAX_OUTPUT_BYTES` | Read `mcp_gitlab/client.py` `get_text`/`get_job_trace`, `settings.py`. 3 regression tests pass incl. the "Content-Length lies/absent" byte-counting fallback | **CLOSED** |
| **R-002** | HIGH | backend | `DEFAULT_PATH_DENY` broadened (`*.env.*`/`.env`, `id_dsa*`/`id_ecdsa*`/`id_ed25519*`, `*.key`/`*.pfx`/`*.p12`/`*.jks`, `.npmrc`/`.netrc`, `*.tfstate`/`*.tfstate.*`/`*.tfvars`), matched against full path **and** basename; `mcp_ingest` imports the same constant so inherits it verbatim | Read `mcp_gitlab/settings.py` + `mcp_ingest/pipeline/redact.py` (`from mcp_gitlab.settings import DEFAULT_PATH_DENY`). 12-param GitLab test + ingest-inheritance test pass | **CLOSED** |
| **R-003** | HIGH | backend | `to_error_envelope()` scrubs `message` and recursively scrubs every string leaf of `details` (`_scrub_recursive`) — the single point every `ErrorEnvelope` is built; `JSONStderrFormatter.format()` scrubs `getMessage()` and `exc_info` — the single formatter every server uses | Read `mcp_common/errors.py` + `logging.py`. 5 regression tests pass incl. end-to-end through `register_tool` (asserts rendered text + `structuredContent`) | **CLOSED** |
| **R-004** | HIGH | sa + qa | Claim relabelled, not re-measured (real measurement blocked behind ADR-0010/HF egress). ADR-0011 A3, `architecture.md` NFR-003 cell, and `signoff/phase-3.md` (§A row + "Giới hạn" + §B item) now all state the recall ≥ 0.95 number is **ANN-vs-brute-force correctness** (anti-false-negative), **not** NFR-003 semantic quality, and mark NFR-003 **UNVERIFIED** | Read all four artifacts. `grep NFR-003 architecture.md` confirms the cell + the three other refs are correctly scoped | **CLOSED (claim)** · NFR-003-semantic carried as Gate-C caveat |
| **R-005** | HIGH | qa | The 10 P1 pgvector E2E tests (TC-045/049/051/052/053/067/068/073/074/077) executed for the **first time** on real PostgreSQL 16.15 + pgvector 0.8.6: **10/10 pass**; full `e2e/` **34/34, 0 skipped**; ADR-0011 A3 recall gate pass (mean 1.0, `hnsw_index_used: true` after `ANALYZE`); pgvector ≥ 0.8 iterative-scan + read-only-role-refusal branches confirmed live; 6/6 emulatable `@live` pass; BE coverage 89.97% | Read `6-verify/regression-report-dev.md` + `evidence/qa-dev/20261001-170716-*` (environment, e2e-pgvector-10xP1, e2e-full, live-emulatable, recall-fake.json). FR-013/AC-001/AC-002 (Journey 3, Must) now have executed behavioural coverage | **CLOSED** |

Independent re-runs on the review host this round:
- `scripts/validate_contract.py <api-contract.yaml>` → **OK, valid contract**.
- `scripts/verify_tool_surface.py` → **42/42 checks passed** (read-only surface, prompts, 6 CLI
  commands, ingest write-credential never in any server env — unchanged by the fixes).
- Named R-fix regression tests → **23 passed** (parametrization of the 10 named tests).
- `ruff check` on the three changed packages → **clean**; `pytest packages/mcp_gitlab mcp_common
  mcp_ingest` → **651 passed, 110 skipped** (skips = the Debian-only `initdb` fixture, R-005
  secondary; no FAILED/ERROR).

All five round-1 blocking HIGH findings are resolved: three by code+test, one by claim correction,
one by first-ever test execution. Error ledger: E-001..E-004 now each carry a `### E-… · verified`
record; `errors.sh open` reports **no open S1/S2 defects**.

## 2. MEDIUM / LOW disposition for Gate C (R-006 … R-027)

Re-assessed every round-1 MEDIUM/LOW against the Gate-C (local v1) bar. **None is a Gate-C blocker.**
Rationale per cluster; all are accepted as deferred residual risk for v1 and belong in the CAB pack.

| ID(s) | Sev | Why not Gate-C blocking | Disposition |
|---|---|---|---|
| R-006, R-007 | MED | Migration-locking hazards (`CHECK` without `NOT VALID`; `CREATE INDEX CONCURRENTLY` impossible inside the per-file transaction). **Latent**: all 6 migrations ship in one commit onto empty tables; no live cluster sits at an intermediate version with real data. First real deploy runs them on an empty `kb` | **Deferred** — fix before the *second* schema change on populated prod data |
| R-008, R-009, R-010 | MED | Missing freshness/`source_uri` indexes; row-by-row ingest; no `statement_timeout` on `mcp_ingest_rw`. Performance/operability at scale; correctness holds. Local v1 corpus is small | **Deferred** |
| R-011 | MED | `vector(1024)` hardcoded while ADR-0010 is `proposed`. Both S2 candidates are 1024d → safe today; `reembed` already guards dim-mismatch. A dimension change needs a documented rewrite runbook | **Deferred** — tie to ADR-0010 finalization |
| R-012 | MED | Contract/impl drift: `prune` makes `--older-than` unconditionally required vs the contract's `anyOf`. Drift is in the **safe** direction (no data-loss path), TC-074 passes. Per CLAUDE.md contract-is-SSOT, must be reconciled (widen impl or SA narrows contract) but it blocks neither safety nor Journey 3 | **Deferred** — reconcile in a follow-up; name the chosen side |
| R-013 | MED | Redis ACL `mcp_ro` grants unbounded reads + `default nopass +@all` on the dev compose. **Not reachable** via any tool (`enforce()` gates every command before I/O). Acceptable for dev/test compose; **must never be reused for staging/shared** | **Deferred** with explicit no-reuse note for env promotion |
| R-014, R-015 | MED | No rerank/lexical fallback; no relevance/faithfulness harness. Both are **declared deferrals** (S3 spike "measurement not done"; TC-065 `# THRESHOLD TBD`, PO-owned). Same family as R-004: retrieval *quality* is explicitly unverified for v1 | **Deferred** — PO scope decision, coupled to the NFR-003 caveat |
| R-016, R-017 | MED | CI is documentation-only (no runner wired), mypy not strict. Governance/signal strength, not a product defect. Relevant only to the assumption that CI enforces gates — it does not; gates run via `make ci` by hand | **Deferred** — note in CAB that gates are run manually, not enforced by a runner |
| R-018..R-027 | LOW | Dead `with_tool_deadline`, block-attempts counter off-by-one, missing `aclose()` in `serve()`, sync calls in the one-shot benchmark, `*_FILE` mode not checked, ATX-heading over-detection, dead doc links (R-025), untracked TC ids (R-026), committed squad backup dir (R-027) | **Deferred** — residual quality items; R-027 (`.gitignore` the backup dir) and R-025 (dead links) are quick wins worth doing opportunistically |

No MEDIUM/LOW is reachable as a live safety or data-loss defect on the local v1 target, so none
converts to a Gate-C blocker. They are recorded here as residual risk for the CAB pack.

## 3. Previously reported — fixed / still open

- **R-001, R-002, R-003** — fixed (code + regression tests), verified on disk and by re-run. **Closed.**
- **R-004** — claim corrected across ADR-0011 A3 / architecture.md / signoff. **Closed** as a
  mislabelling defect; the underlying NFR-003 semantic-quality measurement remains a genuine open
  gap (caveat below), by design, not by error.
- **R-005** — 10 P1 E2E executed and green on real pgvector 0.8.6; full e2e 34/34. **Closed.**
- **R-006 … R-027** — all still open as MEDIUM/LOW; none blocking (§2). Deferred to v1 residual risk.

## 4. Known Gate-C caveats (carry into CAB)

1. **R-004 — NFR-003 semantic quality is UNVERIFIED.** The recall ≥ 0.95 number is ANN-vs-brute-force
   index correctness under the fake provider (yields ~1.0 even if the embedding were noise), not
   retrieval relevance. The real measurement needs a chosen embedding model (ADR-0010 still
   `proposed`, `bge-m3` PROVISIONAL) and a `--provider configured` run, both **blocked by Hugging
   Face egress**. Gate C must be approved with NFR-003 explicitly accepted as unverified by the PO,
   not treated as proven. (Related: R-014/R-015 retrieval-quality deferrals.)
2. **R-005 secondary — 153 package-level Postgres integration tests skip on non-Debian hosts.**
   `packages/conftest.py::_find_pg_bin()` probes only `/usr/lib/postgresql/*/bin/initdb`, so on this
   macOS host (and any non-Debian CI) the package-level `pg_server` fixture skips. The same
   behaviours are now covered end-to-end against real pgvector via the `e2e/`-local `MCP_E2E_PG_URL`
   override, so behaviour is verified; but the package fixture is not portable. Owner backend; a
   small follow-up (honour `PATH`/an env override).
3. **CI not enforced by a runner (R-016).** The 5 verification gates run via `make ci` on demand; no
   CI runner or pre-commit hook blocks a merge that skips them. Gate-C sign-off should state the
   gates were run manually, not enforced automatically.
4. **Migration-locking hazards latent (R-006/R-007)** — safe for the first (empty-table) deploy;
   revisit before the next schema change against populated prod data.

### Verdict (base 9-source scope, round 2): APPROVE — SUPERSEDED by the CHG-001 round-1 verdict below

> Historical record for the base-scope go-live (kept, not deleted). The base 9-source scope was
> APPROVE at round 2: 0 CRITICAL, 0 open HIGH (all five round-1 HIGH resolved and verified), 12
> MEDIUM + 10 LOW all non-blocking and deferred to v1 residual risk, ready for Gate C / release
> subject to the four §4 caveats (caveat 1 NFR-003 requiring explicit PO risk-acceptance at CAB).
> The **current, effective** review verdict for the feature is the CHG-001 round-1 verdict below.

---

# CHG-001 — Review Report (round 1)

- **Date**: 2026-10-02 · Reviewer (squad-reviewer)
- **Branch**: `claude/zealous-johnson-yb3t2q` (local macOS, Docker up). HEAD `b898040`.
- **Diff scope**: the 24-task CHG-001 slice (T-087…T-110) is present as an **uncommitted working tree**
  (untracked `packages/mcp_knowledge/`, `packages/mcp_jira/`, `packages/mcp_common/gateway.py` +
  gateway/grounded tests, `packages/mcp_ingest/migrations/0007*,0008`, `connectors/jira.py`, plus
  modified `mcp_pgvector`, `mcp_common/{envelope,render,errors,logging}`, `scripts/verify_tool_surface.py`,
  `Makefile`, `.env.example`, `ruff.toml`, `uv.lock`). Reviewed against the working tree, not a commit
  (see R-030 for working-tree hygiene).
- **ECC reviewers (dispatched in parallel)**: `ecc:code-reviewer`, `ecc:security-reviewer`,
  `ecc:python-reviewer`, `ecc:database-reviewer`, `ecc:silent-failure-hunter`, `ecc:pr-test-analyzer`,
  `ecc:type-design-analyzer` + the Reviewer's own contract/traceability/scope/invariant checks and a
  manual read of the two choke points, the gateway, Jira read-only surface and the three new migrations.
- **Recurring pattern checked first** (`errors.sh summary --role squad-reviewer`): the dominant class is
  **"a safety guarantee stated in prose/docstring but not enforced at the single choke point every path
  passes through, with no adversarial test for the input it claims to block"** (E-001/002/003, all
  security, all escaped qa-plan+qa-verify; crystallised as **L-001**). I looked for a recurrence of
  exactly this pattern — and found one (R-028).

## Findings

| ID | Severity | Area | File:line | Finding | Failure scenario | Reviewer |
|---|---|---|---|---|---|---|
| R-028 | HIGH — CLOSED (fixed & verified, CHG-001 round 2; see `## Verdict: APPROVE`) | BE / contract (E6 permission) | `packages/mcp_knowledge/src/mcp_knowledge/tools/read_api.py:257` + `:310,:332,:335,:366,:407,:457` | **The permission choke point #1 (`_run_pipeline` → `enforce_permission`) is only on `search_company_knowledge` and `get_jira_context`. The other 6 knowledge-tier content tools — `search_code`, `get_service`, `get_repository`, `find_related_knowledge`, `get_knowledge_summary`, `get_document_version` — read `kb.*` and return content/`source_uri`/provenance **without** passing through choke point #1.** The retrieval SQL legs (`retrieval/sql.py`) carry no `visibility`/permission filter either. So "permission enforced server-side **before content assembly** at a **single choke point** for the knowledge tier" (ADR-0018 §7, ADR-0021, FR-019, the `enforce.py`/`read_api.py` docstrings) is true for 2 of 8 tools, not for the tier. This is a recurrence of the L-001 / E-001/002/003 pattern: guarantee asserted in prose, enforced on one path, not on every path, with no adversarial test on the uncovered paths. | A caller runs `search_code("executive salary")` (or `get_knowledge_summary`, `get_document_version`) and receives the raw chunk content + GitLab `source_uri` of a document they hold no grant on. **In v1 this does not leak a *restricted* document** because ADR-0016 A1 makes the corpus TEAM-ONLY (ingest refuses `visibility != 'team'`; `CallerContext` defaults `is_team_member=True`), so no restricted document exists and `*team*` grants everything — the gate on the 2 grounded tools passes everything anyway. **The moment the permission machinery is actually used for its stated purpose** (RBAC / per-request identity / non-team content, the v1.1 path E6/ADR-0021 was built for), these 6 tools become a default-**allow** bypass around the "single" choke point: a restricted doc leaks via `search_code`/summary/version while `search_company_knowledge` correctly denies it. The adversarial test (TC-090) and the single-choke-point test (TC-091, `test_permission_is_the_single_decision_function`) only exercise the 2 grounded tools and only assert *one `enforce_*` function exists* — neither feeds the restricted-leak input to `search_code`, so the gap is invisible exactly as E-001/002/003 were. | security-reviewer, Reviewer |
| R-029 | MEDIUM | contract | `docs/squad/features/mcp-data-platform/4-design/api-contract.yaml:2771` (`search_code`) | `search_code` is tagged `x-requirements: [FR-002, FR-011]` / `x-acceptance-criteria: [FR-011/AC-002]` but `register.py` maps it under FR-016 and the contract text says "`empty` theo ngữ nghĩa của `kb_semantic_search`". The permission expectation for the non-grounded content tools is **undocumented** in the contract — there is no `x-permission`/visibility note saying these tools intentionally skip choke point #1 (and why that is safe only under TEAM-ONLY). | A future maintainer (or v1.1 RBAC work) reads the contract, sees "knowledge tier, read-only, permission server-side" and assumes `search_code` is filtered like `search_company_knowledge`; the undocumented asymmetry makes R-028 easy to reintroduce or miss. Documentation debt that directly feeds the HIGH. | Reviewer |
| R-030 | LOW | scope | `squad-tg` (repo root, executable shell script); `infra/env-promotion-chg001.md` | Two files outside the `packages/`/`docs/` backend+SA surface are left in the working tree. `infra/env-promotion-chg001.md` is reasonable (backend-owned per its header) but `squad-tg` is a stray root-level executable unrelated to CHG-001. | `squad-tg` could be committed by accident with the slice. Confirm it is intentional tooling or remove it before the commit. No runtime impact. | Reviewer |
| R-031 | LOW | tests | `packages/mcp_knowledge/tests/test_permission_single_chokepoint.py:120` (`test_permission_is_the_single_decision_function`) | The "single choke point" structural test only asserts that the `permission.enforce` module exposes exactly one `enforce_*` callable. It does **not** assert that every content-returning tool routes through it — which is the property L-001 actually requires and which R-028 violates. The test gives false confidence that the single-choke-point invariant holds tier-wide. | The test stays green while 6 tools bypass the gate (R-028). Strengthen it to enumerate the tool surface and assert each content tool invokes the permission seam (or is explicitly, documentedly exempt). | pr-test-analyzer, Reviewer |

## Deep-dive on the two new security guarantees (per the review charter)

**E6 — Permission server-side (default-deny).** The filter itself is correct and well-built:
`enforce_permission` is a pure default-deny *intersection* — a candidate survives only if a
`(document_id, principal, 'read')` grant row exists for a principal the caller holds; absence is
deny, there is no "allow when unsure" branch (`permission/enforce.py`), the SQL (`document_grants`)
is parameterised and read-only, and `chunk_id` used for the assembler intersection is `kb.chunks.id`
(a global PK, so no cross-document collision). On the **two grounded tools** it runs **strictly
before** the grounding gate and exactly once (TC-091 proves ordering; the real-pgvector EXP-1 probe
proves no restricted-doc leak on `search_company_knowledge`). **But the "single choke point for the
tier" claim is false** — see R-028. Verdict on the charter question *"ro ri restricted doc nao qua
candidate/pack/citation/log khong?"*: **not through the two grounded tools; yes in principle through
the other six** — bounded to a non-leak in v1 only by the external TEAM-ONLY corpus invariant
(ADR-0016 A1), not by the choke point.

**E8 — B4 Grounding gate.** Correct and genuinely enforced at one place. `GroundingVerdictGate`
checks evidence **before** confidence (`_has_valid_evidence` → UNKNOWN with the fixed message,
`grade_claims`), so no-evidence ⇒ UNKNOWN is a hard invariant independent of τ; confidence is a
deterministic `retrieval^0.5·agreement^0.3·freshness^0.2` labelled `confidence_basis =
evidence-strength, NOT P(claim true)` with `calibration_status=uncalibrated` (confidence.py), so it
does not assert L-002 falsely; τ constants carry `# THRESHOLD TBD`; CONFLICT exposes every position +
`authority_note` and never merges. One gate, after permission, after compress (GT-5 structural;
GT-7 provenance survives compression). This guarantee is sound and the TBD handling is honest.
**One caveat (not a finding):** the gate lives on the *grounded* path only — which is correct for
grounding, but it means the 6 non-grounded tools also return content with no grounding verdict; that
is by design (they are not "company fact" claims) and acceptable, but it is the same surface as
R-028.

**Read-only (NFR-006/011/012) — PASS.** Independent re-run confirms `verify_tool_surface.py` 50/50,
**62 tools / 0 write tools across all 11 servers**, unknown-write-tool refused at the JSON-RPC layer
on both new servers; Jira `ALLOWED_OPERATIONS` is GET-only (TC-084/094); reranker/embedding offline
with `HF_HUB_OFFLINE=1` and no outbound socket (TC-081); gateway is in-process and opens no listening
socket, audit to stderr only (TC-103, `gateway.py` has no socket/http import by construction).

**One choke point per guarantee (L-001) — PASS for grounding, FAIL for permission** (R-028):
permission is *ordered* correctly relative to grounding (no two parallel gates), but it is not the
*only* path content leaves the server by.

## Previously reported — fixed / still open

- **E-mcp-data-platform-001 … 004** (round 1/2 of the base scope): all `· verified` / CLOSED; NFR-003
  semantic quality carried as the accepted Gate-C residual (unchanged by CHG-001).
- **E-mcp-data-platform-005** (contract snapshot/tool-count drift): fixed, pinned by regression tests;
  independent re-run confirms `verify_tool_surface.py` 50/50 and `test_signoff_surface` 62 tools. CLOSED.
- **E-mcp-data-platform-006** (reconcile `provenance.confidence=None`): fixed by E5; `test_grounded_search`
  `get_jira_context_*` both green in the independent run. CLOSED.
- No previously-open CHG-001 review finding (this is round 1 for the slice).

## Gate-C caveats carried from this review (known, not blocking the *verdict* but required at CAB)

1. **τ (FACT↔LOW_CONFIDENCE) uncalibrated** — `calibration_status=uncalibrated` on every result;
   only τ-independent invariants (GT-1..GT-7) are enforced now. Measuring τ on a real golden-set is a
   Gate-C/NFR-010 task. **Acceptable for v1 go-live** per D-002/ĐK1 and D-004: the no-evidence⇒UNKNOWN
   and CONFLICT invariants hold regardless of τ, so the "no fabricated fact" guarantee is real today;
   only the FACT-vs-LOW gradation is unverified. This is honest TBD handling (L-002), not a defect.
2. **NFR-003 recall / semantic relevance UNVERIFIED** (HF egress 403, `bge-m3` PROVISIONAL) — both
   real-DB probes use the deterministic fake provider; they prove the verdict/permission paths, not
   retrieval quality. **Acceptable for v1 go-live as a CEO-accepted residual** (D-002/ĐK1): the surface
   is read-only, no data-loss path, the measurement is blocked by an environment constraint outside the
   feature. Requires explicit PO/CEO risk-acceptance at CAB — already the standing condition.
3. **Real sources** (Jira/Confluence/GitLab live tenants, `doctor` against real systems, real
   grant/visibility on real data) and **Claude Desktop registration (NFR-005)** are the Gate-C manual
   checklist — not exercisable here.
4. **Package-level Postgres integration (157 initdb + 18 passwordless-DSN skips)** on this macOS host;
   covered end-to-end by the real-pgvector smoke + EXP-1. Portable `_find_pg_bin()` is a carried backend
   follow-up (R-005 secondary), not blocking.
5. **DK2 migration-locking** (R-006/R-007) was a condition for E1; migrations 0007/0007b/0008 follow it
   (0007 constant-default `content_tsv`, `NOT VALID`+`VALIDATE` split for the populated-table CHECK,
   0007b `CREATE INDEX CONCURRENTLY` outside a transaction). Spot-checked and consistent with ADR-0022/DK2.

### Verdict (CHG-001 round 1): CHANGES_REQUESTED — SUPERSEDED by the CHG-001 round-2 verdict below

> Historical record for CHG-001 round 1 (kept, not deleted). The **current, effective** review
> verdict for the feature is the CHG-001 round-2 `## Verdict: APPROVE` below.

0 CRITICAL, **1 HIGH open (R-028)**, 1 MEDIUM, 2 LOW. The HIGH blocks the verdict: the permission
server-side guarantee — one of the two new security capabilities this change exists to deliver — is
not enforced at the single choke point the design claims; it holds on 2 of 8 knowledge-tier content
tools and is kept safe in v1 only by the external TEAM-ONLY corpus invariant, with no adversarial test
on the uncovered six. This is a recurrence of the exact L-001 / E-001/002/003 pattern.

**On the Gate-C question (tau uncalibrated + NFR-003 UNVERIFIED):** those two are **acceptable** for a
v1 local go-live as already-decided residuals (D-002/ĐK1, D-004) — they do not block this verdict. What
blocks it is R-028, which is a *code/contract* gap in a stated security guarantee, not a measurement
caveat.

**To reach APPROVE**, the owner must either (a) route the 6 non-grounded content tools through the
single permission choke point (preferred — makes the guarantee tier-wide and future-proofs the v1.1
RBAC path), or (b) if v1 deliberately relies on the TEAM-ONLY invariant, make that reliance explicit:
document in the contract + ADR-0017/0021 that these tools are permission-exempt *because and only
while* the corpus is TEAM-ONLY, assert the `visibility <> 'team'` count-0 invariant as a startup/CI
check, and add an adversarial test proving `search_code`/summary/version behave correctly the day a
non-team document (or a non-`*team*` caller) exists. Either way R-031 (strengthen the single-choke-
point test to cover every content tool) and R-029 (document the asymmetry) close with it.

### Error-ledger entries opened by this review

See `records/errors.md` → `## E-mcp-data-platform-007` (R-028, security, HIGH).

---

# CHG-001 — Review Report (round 2)

- **Date**: 2026-10-02 · Reviewer (squad-reviewer)
- **Branch**: `claude/zealous-johnson-yb3t2q` (local macOS, Docker UP — `mcp-dev-postgres` healthy,
  pgvector present). HEAD unchanged working tree.
- **Scope since round 1**: the single blocking HIGH **R-C-001 / R-028 / E-007** (permission choke
  point wired on only 2 of 8 content tools), plus the two coupled items **R-C-002 / R-029** (wording
  asymmetry) and **R-C-004 / R-031** (per-tool test coverage), and the LOW **R-C-003 / R-030**
  (`squad-tg` working-tree hygiene). The round-1 MEDIUM/LOW residuals (R-029 MEDIUM, R-030 LOW) and
  the Gate-C caveats carried from round 1.
- **What round 2 did**: re-verified each item against the code/doc/contract on disk and **re-ran the
  relevant checks on this host — the report numbers were not trusted, they were reproduced.** No code,
  no `state.json`, no `records/decisions.md` edited; the Reviewer appended only the one
  `### E-mcp-data-platform-007 · verified` record to the error ledger and this report section.
- **Recurring-pattern scan first** (`scripts/squad/errors.sh summary --role squad-reviewer`): the
  dominant class is still **L-001** — "a safety guarantee asserted in prose/docstring, enforced on
  one path but not at the single choke point every path passes through, with no adversarial test for
  the input it claims to block" (E-001/002/003, and E-007 = R-028 is an explicit recurrence). Round 2
  checked specifically that the fix removes the recurrence, not just the one reported symptom.

## 1. R-C-001 (R-028 / E-007) — permission choke point: VERIFIED tier-wide

Read `permission/enforce.py` and `tools/read_api.py` in full. The fix is option (a): all 8
knowledge-tier content tools now route through the **one** `enforce_permission` default-deny
decision over the **one** `document_grants` read, before any content / `source_uri` / provenance is
returned, and before the grounding gate.

| Tool | Path to the single decision | Default-deny behaviour verified |
|---|---|---|
| `search_company_knowledge` | `_run_pipeline` → `_permission_filter` → `load_grants` → `enforce_permission` via the one `ContextPackAssembler.permission_filter` hook, **strictly before** the grounding gate (assembler applies permission, then passes only surviving chunks to the gate — confirmed in `pack/assembler.py::assemble`) | restricted candidate dropped before pack/claim/citation |
| `get_jira_context` | resolves the ONE `PermissionFilter` once, filters candidates, and **reuses the same resolved filter for both the pack and the reconcile snapshot** (`_snapshot_value_for` runs on the already-filtered candidates) — closes the second, subtler snapshot-side bypass | one `document_grants` read; snapshot cannot expose a denied doc |
| `search_code` | `_permitted_document_ids` → `load_grants_for_document_ids` → filters candidates before building items/citations | denied doc → not an item, not a citation |
| `get_service` / `get_repository` | `_get_entity` → `_document_gate_denies(entity.document_id)` → `not_found` | denied doc indistinguishable from missing — no content, no existence oracle |
| `find_related_knowledge` | `_permitted_document_ids([root, *edges])`; denied root → `not_found`; each edge whose backing doc is denied is dropped (per-edge default-deny); a NULL-doc edge carries no restricted content so it is kept | per-edge default-deny, permitted traversal unbroken |
| `get_knowledge_summary` | `_permitted_document_ids(provenance_docs)`; **any** denied provenance doc → `not_found` (a summary distils its sources) | all-sources-permitted-or-nothing |
| `get_document_version` | `_permits_document` → `not_found` | denied doc → no version row / content_hash / source_uri |

Confirmed structurally:

- **One choke point, no default-allow branch.** `load_grants` and the six non-grounded tools both
  funnel through `load_grants_for_document_ids` — a single parameterised `document_grants` SELECT
  (`retrieval/sql.py`: `grant_type='read'`, `d.deleted_at IS NULL`, principals ∈ caller's set) and the
  single `enforce_permission` intersection. Absence of a grant row **is** the deny; there is no
  "allow when unsure" path. An empty id/principal set short-circuits to deny-everything without a DB
  hit.
- **Permission before grounding.** `pack/assembler.py::assemble` runs the permission filter, keeps
  only the surviving `chunk_id`s, then calls the grounding gate on that subset. `test_assembler_
  applies_permission_strictly_before_gate` instruments both hooks and asserts the order
  `["permission", "gate"]` and that the gate never sees a denied candidate.
- **The retrieval SQL legs carry no competing visibility logic** — correct: permission is one
  Python decision over `document_grants`, not duplicated (and therefore not divergeable) in the
  vector/keyword legs.
- **The test seam cannot diverge from production.** A pinned `PermissionFilter` is honoured by the
  non-grounded tools too (via a per-document `_doc_probe`), so a test's deny-all filter makes every
  tool withhold content exactly as production enforcement would.

### Independent re-runs on the review host (numbers reproduced, not trusted)

The live tests skip by default on this host because `mcp_admin` needs a password the shell does not
carry; supplying it (`PGPASSWORD`, container value) reproduced the backend's green live run:

- `test_permission_live_pgvector.py` (8 tools, real pgvector 0.8.x through the read-only
  `mcp_query_ro` role, throw-away DB) → **8 passed, 0 skipped**. The restricted `source_uri` / secret
  / `document_id` appear in **no** tool's output; the permitted doc resolves everywhere it should.
  This is the live proof of R-C-001.
- `test_permission_single_chokepoint.py` + `test_permission_adversarial.py` +
  `test_permission_enforce.py` (TC-090 adversarial, TC-091 single-choke-point, per-tool leak probes,
  mixed-graph per-edge) → **30 passed**; with the live DB, `test_every_content_tool_consults_the_
  single_permission_seam` enumerates all 8 tools and asserts each hits the one `document_grants`
  seam.
- Full `packages/mcp_knowledge` → **202 passed, 0 skipped** (live DB).
- `scripts/verify_tool_surface.py` → **50/50 checks passed** (62 tools, 0 write tools).
- `make ci` → green (lint, typecheck, coverage, validate-contract all passed; the final `readonly`
  stage **209 passed**; `make` stops on first failure, so the earlier stages are confirmed green).
- Full `pytest packages/ e2e/` with the live DB → **2288 passed, 179 skipped, 0 failed** (the extra
  passes over the reported 2264 are the live tests this host could reach; the remaining skips are the
  `initdb` package-level E2E — the carried R-005-secondary caveat, covered by the live suite).

**R-C-001 / R-028 / E-007: CLOSED.** The guarantee is now true as written for all 8 content tools;
the L-001 recurrence is removed, and (unlike the round-1 state) there is a per-tool adversarial test
on every previously-uncovered path, including live.

## 2. Coupled items

- **R-C-002 / R-029 (wording asymmetry) — CLOSED.** All 8 content tools now carry an `x-permission`
  annotation in `api-contract.yaml` (verified at the 8 `operationId`s) stating "server-side
  default-deny via `enforce_permission` (choke point #1) before content/provenance; ONE choke point
  for all 8 content tools; team-only corpus = defence-in-depth, not the sole barrier", with
  tool-specific detail (jira: shared filter for pack+snapshot; related: per-edge; summary/version:
  not_found). `FR-019` (requirements.md:229-235, 337), `architecture.md` (:288), `ADR-0018 §7`
  (:202-208) and `ADR-0021` (:23-29, 60) all now state the single choke point is tier-wide for all 8
  tools and name the default-allow bypass that option (a) closed. No "grounded tools only" scoping
  remains — the asymmetry R-029 flagged is gone.
- **R-C-004 / R-031 (per-tool test) — CLOSED.** The single-choke-point test no longer only asserts
  "one `enforce_*` symbol exists": `test_every_content_tool_consults_the_single_permission_seam`
  enumerates the 8-tool surface and asserts each consults the seam, and `test_permission_adversarial.py`
  / `test_permission_live_pgvector.py` carry a restricted-leak probe per tool plus a mixed-graph
  per-edge test. The property L-001 actually requires is now pinned for every tool, offline and live.
- **R-C-003 / R-030 (`squad-tg`) — ACCEPTED / CLOSED.** `git status` confirms `squad-tg` is `??`
  (untracked), not tracked, and nothing is staged on `claude/zealous-johnson-yb3t2q`. It is a
  root-level shortcut outside `packages/`, not build output, and will not enter a commit unless
  explicitly added. The DM's disposition (accept) is correct; no action required.

## 3. Residual MEDIUM / LOW and Gate-C caveats

No MEDIUM/LOW is a Gate-C blocker. The round-1 MEDIUM/LOW (R-006…R-027) stand as already-deferred v1
residual risk (unchanged — the CHG-001 slice did not reopen them; the migration-locking DK2 items
R-006/R-007 are addressed for the populated-table case by 0007/0007b/0008, spot-checked consistent).
R-029 (MEDIUM, now closed above) and R-030 (LOW, accepted above) are the only CHG-001 non-HIGH items.

The two Gate-C caveats this change carries are **evidence caveats, not code defects**, and are the
already-decided v1 residuals (D-002/ĐK1, D-004) — they do **not** block the verdict:

1. **τ (FACT↔LOW_CONFIDENCE) uncalibrated.** Every grounded result is labelled
   `calibration_status=uncalibrated`; only the τ-**independent** invariants are enforced today
   (no-evidence ⇒ UNKNOWN with the fixed message; CONFLICT exposes every position + `authority_note`,
   never merges — `grounding/verdict.py` checks evidence before confidence). So "no fabricated fact"
   is a real guarantee now; only the FACT-vs-LOW gradation is unmeasured. Honest TBD handling (L-002),
   acceptable for v1, measured on a golden set as a Gate-C/NFR-010 follow-up.
2. **NFR-003 recall / semantic relevance UNVERIFIED.** Both real-DB probes use the deterministic
   fake provider; they prove the permission/verdict paths, not retrieval quality. The real
   measurement needs a chosen embedding model (ADR-0010 `proposed`, `bge-m3` PROVISIONAL) and a
   `--provider configured` run, both blocked by Hugging Face egress (403). Acceptable for v1 as a
   CEO/PO-accepted residual (D-002/ĐK1): read-only surface, no data-loss path, blocked by an
   environment constraint outside the feature. **Requires explicit PO/CEO risk-acceptance at CAB** —
   the standing condition, carried, not newly introduced. (Related: R-014/R-015 retrieval-quality
   deferrals.)

Also carried (operational, from round 1): CI is run via `make ci` by hand, not enforced by a runner
(R-016); the `initdb` package-level Postgres fixture is non-portable on non-Debian hosts (R-005
secondary) — covered end-to-end by the live suite; real sources (live Jira/Confluence/GitLab tenants,
`doctor`, real grants on real data) and Claude Desktop registration (NFR-005) are the Gate-C manual
checklist, not exercisable here.

## 4. Previously reported — fixed / still open

- **R-C-001 / R-028 / E-007** — fixed (option a: tier-wide choke point + snapshot-side fix) and
  verified on disk and by re-run (offline 30 + live 8 + full suite 202). **CLOSED.**
- **R-C-002 / R-029** — wording synchronised across contract/FR/architecture/ADR-0018/ADR-0021.
  **CLOSED.**
- **R-C-004 / R-031** — per-tool single-choke-point + adversarial tests, offline and live. **CLOSED.**
- **R-C-003 / R-030** — `squad-tg` untracked, not committed, accepted by DM. **CLOSED (accepted).**
- **Base-scope E-001..E-005** — closed/verified in the base rounds; **E-006** (reconcile
  `provenance.confidence=None`) fixed by E5, `test_grounded_search` green in the independent run.
- **Round-1 base MEDIUM/LOW R-006…R-027** — still open as deferred v1 residual risk; none blocking.

### Verdict (CHG-001 round 2): APPROVE — SUPERSEDED by the CHG-003 round-1 verdict below

(CHG-001 round 2 — was the effective verdict for the CHG-001 slice; the **current, effective**
feature verdict is now the CHG-003 round-1 `## Verdict: APPROVE` at the end of this report. CHG-001
remains APPROVE; this heading is demoted only so a single current `## Verdict:` line is parsed.)

0 CRITICAL, **0 open HIGH**, 0 open MEDIUM/LOW blocker for CHG-001. The single round-1 blocker
(R-028, the permission choke point enforced on only 2 of 8 tools) is resolved by making the single
`enforce_permission` default-deny decision genuinely tier-wide across all 8 content tools, with the
snapshot-side bypass closed, the contract/ADR/FR wording synchronised, and a per-tool adversarial
test offline **and** on real pgvector — all independently reproduced on this host (`make ci` green,
`verify_tool_surface` 50/50, live permission suite 8/8, full suite 2288 passed / 0 failed).

**On the Gate-C question (τ uncalibrated + NFR-003 UNVERIFIED):** both are **accepted** as
already-decided v1 residuals (D-002/ĐK1, D-004), not blockers. The τ-independent "no fabricated fact"
guarantee holds today; NFR-003 semantic quality is a documented, environment-blocked gap requiring an
explicit PO/CEO risk-acceptance at CAB. The feature is **ready for Gate C** subject to that standing
risk-acceptance and the manual Gate-C checklist (real sources, Claude Desktop registration).

### Error-ledger entries updated by this review

`records/errors.md` → `### E-mcp-data-platform-007 · verified` appended (R-C-001 fix confirmed on
disk and by live + offline re-run). No open S1/S2 defect remains for the CHG-001 slice.

---

# mcp-data-platform — Review Report (CHG-003, round 1)

> Reviewer, mode review. Scope: CHG-003 (real ingestion + real egress, CLI-driven, 9-source runbook,
> Confluence first; lean, CEO Gate-1 Option B). CHG-003 is uncommitted in the working tree (base commit
> `b898040`); diff scope = `git status` working-tree change set (31 modified + untracked under `packages/`),
> the ADR-0023 egress/credential/model surface, and the Appendix-A runbook. SECURITY as primary lens —
> the recurring defect class on this feature is security (E-001/002/003 security, E-007 permission bypass,
> E-009 token-wiring gap). Ran `errors.sh summary --role squad-reviewer` + checked for the L-001 RECURRING
> pattern (guarantee in prose, not enforced at the one choke point, no adversarial test) first.

## Diff scope reviewed
- **mcp_common** (new/changed): `egress.py` (NEW — the one default-deny choke point), `http.py`
  (`build_client(enforce_egress=…)` hook), `redact.py` (`register_secret`/`register_dsn_secret` +
  value-based pass), `config.py`, `envelope.py`, `render.py`, `gateway.py` (NEW, in-process gateway).
- **11 credential-bearing client constructors**: `mcp_confluence`, `mcp_jira`, `mcp_gitlab`,
  `mcp_opensearch`, `mcp_kibana`, `mcp_redis`, `mcp_kafka`, `mcp_cloudwatch`, `mcp_sqs_sns`,
  `mcp_pgvector`, `mcp_knowledge` — each calls `register_secret()`/`register_dsn_secret()` at `__init__`.
- **Ingest connectors**: `connectors/{confluence,gitlab,opensearch,jira}.py` (`build()` → `enforce_egress=True`).
- **Model path**: `embedding/download.py` (NEW — HF egress gate + `HF_HUB_OFFLINE` flip/restore).
- **OpenSearch**: `ReadOnlyTransport` → `EgressGuardedReadOnlyTransport` subclass runs `check_egress`.
- Runbook `architecture.md` Appendix A; `requirements.md` FR-023..027/NFR-013..014; `api-contract.yaml`.

## Findings

| ID | Severity | Area | File:line | Finding | Failure scenario | Reviewer |
|---|---|---|---|---|---|---|
| R-C3-001 | MEDIUM | BE | `packages/mcp_ingest/src/mcp_ingest/embedding/http.py:41` | `HttpEmbeddingProvider` builds a raw `httpx.Client(...)` and `_post()` POSTs to the operator-configured `MCP_INGEST_EMBEDDING_URL` **without** passing through `mcp_common.egress.check_egress` (0 egress refs in the file). This is an outbound path on the ingest/embed surface that bypasses the single default-deny choke point ADR-0023 §6a claims governs "the ingest/model egress path". | If an operator sets `MCP_INGEST_EMBEDDING_PROVIDER=http` + a `url`, `mcp-ingest run` dials an arbitrary host with the embedding API key attached, not refused by the allow-list — a default-allow egress hole relative to the §6a prose. | reviewer (self), security lens |
| R-C3-002 | LOW | contract/docs | `docs/adr/0023-chg003-egress-ingestion-deviation.md §6a`; `architecture.md:1395` A.4 | §6a / A.4 state egress enforcement is a single choke point on "the ingest/model egress path" but do not name the `provider=http` embedding path as a (deliberately out-of-approved-scope) exception. Prose reads broader than the enforcement. | A future reader assumes all ingest-path egress is gated and enables `provider=http`, believing it is allow-list-governed when it is not. | reviewer (self) |

No CRITICAL or HIGH findings. R-C3-001 is **not** blocking: the CHG-003-approved embedding path is
`provider=local` (the default) + the one-time HF download, which **is** gated by `check_egress` in
`download.py`; `embedding/http.py` is pre-existing (last touched `fb53599`, phase3a — NOT in the CHG-003
diff) and off by default; the Appendix-A runbook never instructs an operator to use `provider=http`; and
no FR/NFR threshold is falsified — NFR-013 is scoped precisely to "the ingest-pull / model-download path"
and does not claim the http provider is gated. It is recorded as residual risk for the CAB pack, not opened
as a defect (not introduced by CHG-003; approved path is sound). Recommended future hardening: route
`HttpEmbeddingProvider` through `build_client(enforce_egress=True)` or add a `check_egress` gate so the
single-choke-point property is literally true for every ingest-path outbound call.

## What PASSED (verified on this host, not taken on trust)

**1. Egress default-deny TRUE single choke point (L-001).** `check_egress` in `egress.py` is the one gate:
empty allow-list ⇒ deny-all; `fnmatch` host match (`*.atlassian.net` admits `tnexwm.atlassian.net`, rejects
`atlassian.net.evil.example` and a bare `atlassian.net`); refuses **before** any socket. Wired at exactly
three seams, all calling the one function: `http.build_client(enforce_egress=True)` request hook,
`EgressGuardedReadOnlyTransport.perform_request` (OpenSearch SDK), and `download_model` (HF). Grepped
`httpx/requests/urllib/aiohttp/socket/http.client` across all packages: the 9 MCP servers build their client
with the default `enforce_egress=False` and reach only their own upstream read API (boto3/redis/aiokafka for
the 5 live-only servers = own-upstream, stdio, per ADR-0023 §6a — not a bypass). **One real bypass of the
"single choke point" prose found: R-C3-001 (`embedding/http.py`), classified MEDIUM/non-blocking above.**
Re-ran: `test_egress_default_deny.py`, `test_egress_allowlist.py`, `test_servers_no_egress_no_port.py`,
`test_egress_wired.py`, `test_opensearch/test_egress_guarded_transport.py` — all green.

**2. Credential handling.** Token read only from env/`*_FILE` (ADR-0005); `register_secret()` wired at **all
11** credential-bearing constructors (grep-confirmed on disk: confluence:81, jira:112, gitlab:142,
opensearch:267, kibana:88, redis:209, kafka:215, cloudwatch:189, sqs_sns:202; `register_dsn_secret` pgvector:180,
knowledge:108). Value-based scrub runs **first** in `scrub()` and covers an **opaque** token (no `ATATT3`
shape, not high-entropy) on both the result boundary (`to_error_envelope`) and stderr (`JSONStderrFormatter`).
`doctor` refuses a write-capable account (`ConfluenceClient.verify_credentials` → `credential_check` → startup
gate raises `SourceMisconfiguredError`; escape hatch logs WARN). Re-ran: `test_token_never_leaks.py` (TC-115,
9) + `test_token_scrub_wiring.py` (E-009, 5) = 14 passed; `test_credential_readonly_cloud.py` (TC-116, incl.
`doctor` arms, token-never-prints) green.

**3. Read-only-to-source + stdio / NFR-005 not regressed.** `verify_tool_surface.py` → **50/50**, **0 write
tools** across all 12 servers incl. Jira, **62 tools == contract**, snapshot == contract, 4 prompts, 6
ingest commands, `mcp_ingest_rw` named in no server env and read by no server. The 4 ingestable connectors
pass `enforce_egress=True` on the pull (TC-112 structural no-bypass); live-only 5 are reachable+read-only+
registered only (connector registry = {confluence, gitlab, opensearch, jira}). CHG-001 permission choke #1 +
grounding gate #2 untouched by the ingest-content path (real Confluence content lands in `kb.*` subject to the
same `enforce_permission`, deny-glob, team-only default-deny).

**4. Model download.** `download_model` gates `huggingface.co` through the one `check_egress` (a non-HF host
on the model path is refused); `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` flip to `0` **only** inside
`online_for_download()` and are restored in `finally` (incl. deleting a previously-unset var); serving opens
no socket; the real 2 GB `bge-m3` download is a `@live` arm gated behind `MCP_INGEST_ALLOW_LIVE_EGRESS=true` +
`MCP_LIVE_TESTS` and excluded from `make ci` (CI uses an injected `model_factory` stub). ADR-0010 stays
**provisional**; NFR-003 honest — no invented recall/τ (L-002/E-004 not recurring; `calibration_status:
uncalibrated` carried). Re-ran: `test_model_egress_separable.py`, `test_model_download_offline_flip.py`
(1 `@live` skip, correctly gated), `test_nfr003_conditional.py`, `test_live_egress_gated_off_in_ci.py` green.

**5. Runbook correctness (Appendix A).** The 9 commands match the real CLI (`mcp-<src> doctor`,
`mcp-ingest run --source <src>`, `mcp-ingest status --json`, `config-emit`); ingestable (4) vs live-only (5)
split is honest and matches the on-disk connector registry; no live-only source is claimed as ingested; the
embedding caveat is labelled (enabling ≠ proving). Re-ran: `test_runbook_ingestable.py`,
`test_runbook_live_only.py` green.

**6. Contract + tool surface.** Additive only (62 non-CLI ops; Jira +5, knowledge +8 from CHG-001; CHG-003
adds no new tool), 0 write tools, snapshots == contract, `verify_tool_surface` 50/50. `check.sh contract` PASS.

**7. Error ledger.** E-mcp-data-platform-009 (token-wiring gap, L-001 class) genuinely closed — re-ran
`test_token_scrub_wiring.py` (opaque secret scrubbed from result + stderr by construction only, baseline proves
it is not scrubbed before a client is built) + grep of all 11 constructors; the `verified` record matches the
code on disk. E-008 (FR-005 missing E2E) correctly scoped S3 pre-existing/deferred (not a CHG-003 blocker;
CHG-003's own Must FRs all have E2E TCs). `check.sh errors` PASS. No open S1/S2 remains for the CHG-003 slice.

## Previously reported — fixed / still open
- **E-009 (token-wiring gap)** — FIXED + verified; re-confirmed on this host (14 passed). CLOSED.
- **E-008 (FR-005 no E2E)** — still open, S3, deferred to CTO (base-TC renumber forbidden mid-change); carried
  as a Gate-C residual, non-blocking for CHG-003.
- No CHG-001 CRITICAL/HIGH re-opened; CHG-001 E-001/002/003/004/005/006/007 remain closed.

## Verdict: APPROVE

0 CRITICAL, 0 HIGH, 1 MEDIUM (R-C3-001, non-blocking residual), 1 LOW (R-C3-002). No blocking finding; no
E-010 opened (R-C3-001 is pre-existing, off-by-default, outside the approved `provider=local` path, and
falsifies no AC/NFR). Verdict is APPROVE.

### Gate-C caveats to carry
- **NFR-003 semantic quality UNVERIFIED** — opening HF egress only *enables* measurement; the real recall
  number + calibrated FACT↔LOW_CONFIDENCE τ require the spike-S2 bake-off on a real golden-set (D-002/ĐK1,
  NFR-014). `calibration_status: uncalibrated` carried; no number invented.
- **τ (FACT↔LOW_CONFIDENCE) uncalibrated** — tracked, non-blocking.
- **`@live` egress arms** (real Atlassian pull, real 2 GB `bge-m3` download) are operator steps gated behind
  `MCP_INGEST_ALLOW_LIVE_EGRESS=true` + `MCP_LIVE_TESTS`, never in `make ci` — not exercised by this review.
- **E-008** (FR-005 missing dedicated E2E) — S3, deferred.
- **R-C3-001** (`HttpEmbeddingProvider` not egress-gated) — MEDIUM residual; harden before any `provider=http`
  use.
