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

## Verdict: APPROVE

0 CRITICAL, 0 open HIGH (all five round-1 HIGH resolved and verified), 12 MEDIUM + 10 LOW all
non-blocking and deferred to v1 residual risk. The feature is ready for Gate C / release, subject to
the four caveats in §4 — of which caveat 1 (NFR-003 semantic quality unverified) requires an
explicit PO risk-acceptance at CAB.
