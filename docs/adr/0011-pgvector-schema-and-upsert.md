# ADR-0011: Schema pgvector, HNSW cosine, upsert idempotent theo content hash

**Date**: 2026-10-01
**Status**: accepted
**Deciders**: SA (squad-sa)

## Context

FR-011 cần vector similarity search trả về top-N chunk kèm metadata resolve về nguồn gốc;
FR-012 AC-003 yêu cầu nội dung đã ingest mà thay đổi ở nguồn thì **được cập nhật/thay thế,
không nhân bản vô hạn**; BR-005 yêu cầu giữ source metadata (source type, original id/URL,
timestamp) cho mọi chunk. Đây là thành phần duy nhất của feature có dữ liệu bền vững, nên là
chỗ duy nhất cần migration.

## Decision

Schema `kb` trong Postgres (extension `vector`):

```sql
CREATE TABLE kb.documents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type       text NOT NULL,          -- confluence|gitlab|opensearch|...
  source_id         text NOT NULL,          -- id gốc ổn định ở nguồn
  source_uri        text NOT NULL,          -- URL resolve được, dùng làm citation
  title             text,
  container         text,                   -- space key / project path
  author            text,
  content_hash      text NOT NULL,          -- sha256 nội dung đã chuẩn hoá
  source_updated_at timestamptz,
  ingested_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,            -- tombstone khi nguồn đã xoá
  metadata          jsonb NOT NULL DEFAULT '{}',
  UNIQUE (source_type, source_id)
);

CREATE TABLE kb.chunks (
  id              bigserial PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES kb.documents(id) ON DELETE CASCADE,
  chunk_index     int  NOT NULL,
  content         text NOT NULL,
  token_count     int,
  heading_path    text,                     -- ngữ cảnh mục để hiển thị citation chính xác
  embedding       vector(1024) NOT NULL,
  embedding_model text NOT NULL,
  embedded_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, chunk_index)
);
CREATE INDEX ON kb.chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX ON kb.chunks (document_id);

CREATE TABLE kb.ingest_runs (
  id uuid PRIMARY KEY, source_type text NOT NULL, started_at timestamptz NOT NULL,
  finished_at timestamptz, status text NOT NULL,      -- running|success|partial|failed
  documents_seen int DEFAULT 0, documents_upserted int DEFAULT 0,
  documents_skipped int DEFAULT 0, documents_failed int DEFAULT 0,
  chunks_written int DEFAULT 0, error_summary jsonb DEFAULT '[]'
);

CREATE TABLE kb.ingest_source_state (
  source_type text PRIMARY KEY, cursor jsonb NOT NULL DEFAULT '{}',
  last_success_at timestamptz, last_run_id uuid REFERENCES kb.ingest_runs(id)
);

CREATE TABLE kb.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
```

- **Hai role**: `mcp_ingest_rw` (INSERT/UPDATE/DELETE trên schema `kb`) dùng bởi pipeline;
  `mcp_query_ro` (chỉ `SELECT`, `ALTER ROLE … SET default_transaction_read_only = on`) dùng bởi
  server `mcp-pgvector`. Đây là lớp 3 read-only của ADR-0003 cho FR-011 AC-003.
- **Upsert idempotent**: tính `content_hash` trên nội dung đã chuẩn hoá. Nếu document đã tồn
  tại và hash không đổi → skip (không embed lại, tiết kiệm chi phí). Nếu hash đổi → trong một
  transaction: `UPDATE documents`, `DELETE FROM chunks WHERE document_id = …`, `INSERT` chunk mới.
- **Xoá ở nguồn**: chỉ pass "full reconcile" (chạy theo ngày) mới set `deleted_at` cho document
  không còn thấy; truy vấn FR-011 luôn filter `deleted_at IS NULL`.
- **Migration**: file SQL đánh số trong `packages/mcp_ingest/migrations/NNNN_*.sql`, áp bằng
  `mcp-ingest db upgrade`, theo dõi qua `kb.schema_migrations`.
- Query FR-011: `ORDER BY embedding <=> $query_vec LIMIT $k`, similarity = `1 - distance`,
  áp `min_similarity` (mặc định 0.30) và trả `status=empty` nếu không chunk nào vượt ngưỡng.

## Alternatives Considered

### Alternative 1: IVFFlat thay HNSW
- **Pros**: Build index nhanh hơn, ít RAM hơn.
- **Cons**: Cần `lists` tuning theo số hàng và phải REINDEX khi dữ liệu tăng; recall kém hơn ở
  cùng độ trễ.
- **Why not**: Khối lượng dự kiến (chục–trăm nghìn chunk) hoàn toàn phù hợp HNSW; recall quan
  trọng hơn thời gian build cho use case này.

### Alternative 2: Vector DB riêng (Qdrant/Weaviate/Milvus)
- **Pros**: Tính năng filter/hybrid tốt hơn.
- **Cons**: PRD **chỉ định** Postgres+pgvector là một trong 9 nguồn; thêm hạ tầng mới.
- **Why not**: Ngoài scope; pgvector đủ và đã nằm trong yêu cầu.

### Alternative 3: Một bảng phẳng (chunk mang luôn mọi metadata nguồn)
- **Pros**: Query một bảng, không JOIN.
- **Cons**: Metadata bị nhân bản theo số chunk; cập nhật document phải sửa n hàng; khó làm
  tombstone và `kb_get_document`.
- **Why not**: Chi phí JOIN không đáng kể so với lợi ích về tính đúng đắn của upsert.

### Alternative 4: Alembic cho migration
- **Pros**: Chuẩn công nghiệp, autogenerate.
- **Cons**: Kéo theo SQLAlchemy vào một pipeline chỉ dùng SQL thuần; autogenerate không hiểu
  `vector`/HNSW nên vẫn phải viết tay.
- **Why not**: 4 bảng, SQL thuần + runner ~50 dòng là đủ và minh bạch hơn.

## Consequences

### Positive
- FR-012 AC-003 được bảo đảm bằng ràng buộc `UNIQUE (source_type, source_id)` + hash check,
  không phụ thuộc logic đúng của người viết code crawl.
- `ingest_runs` / `ingest_source_state` là nguồn dữ liệu cho `kb_list_sources` và
  `mcp-ingest status` → phục vụ verification của NFR-004 (freshness).
- Role tách đôi làm FR-011 AC-003 đúng ở tầng database, không chỉ ở tầng ứng dụng.

### Negative
- Số chiều 1024 nằm cứng trong DDL → phụ thuộc ADR-0010; đổi model khác chiều cần migration mới.
- Tombstone chỉ chính xác sau pass full reconcile → document đã xoá có thể còn xuất hiện tối đa
  một chu kỳ reconcile.

### Risks
- HNSW index build tốn RAM khi số chunk lớn. Giảm thiểu: `maintenance_work_mem` đặt riêng cho
  session build index; build sau khi nạp lô lớn đầu tiên.
- Không có hybrid (BM25 + vector) → truy vấn theo từ khoá chính xác (tên hàm, mã lỗi) có thể
  recall kém. Ghi nhận spike: thêm cột `tsvector` + RRF ở giai đoạn sau.

## Amendments (sau design review 2026-10-01)

### A1 — Bổ sung cột: tombstone không implement được bằng DDL ban đầu
DDL ban đầu không có cột nào ghi "đã thấy ở run nào", nên câu tombstone trong architecture.md
(`WHERE không thấy trong lần crawl này`) là không viết được. Bổ sung vào `kb.documents`:

```sql
ALTER TABLE kb.documents
  ADD COLUMN last_seen_run_id   uuid REFERENCES kb.ingest_runs(id),
  ADD COLUMN last_seen_at       timestamptz,
  ADD COLUMN chunk_config_hash  text NOT NULL DEFAULT '',
  ADD COLUMN visibility         text NOT NULL DEFAULT 'team';
CREATE INDEX ON kb.documents (source_type, last_seen_run_id);
```
- `last_seen_*` được set cho **mọi** document đã thấy, kể cả document bị skip vì hash không đổi.
- `chunk_config_hash` làm khoá skip cùng `content_hash`: đổi chunk size/overlap ⇒ re-chunk
  (trước đó đổi cấu hình chunker thì document cũ **không bao giờ** được chia lại → kho không
  đồng nhất mà không có cách nào biết).
- `visibility` phục vụ filter phân quyền tương lai (xem ADR-0016).

Câu tombstone chuẩn (chạy đúng một lần, scoped theo nguồn):
```sql
UPDATE kb.documents SET deleted_at = now()
WHERE source_type = $1 AND last_seen_run_id IS DISTINCT FROM $2 AND deleted_at IS NULL;
```

### A2 — Chunk phải bị xoá vật lý khi tombstone; index có điều kiện
`deleted_at` chỉ đặt trên `documents` nên chunk của nội dung đã xoá **tồn tại mãi**: chúng
chiếm slot ứng viên của ANN scan (làm hỏng recall) và làm index phình theo thời gian.
Quyết định: tombstone **xoá vật lý chunk** (`DELETE FROM kb.chunks WHERE document_id = …`)
ngay trong transaction tombstone; hàng `documents` giữ lại làm bia mộ để không re-ingest ngay.
Index HNSW vì vậy không cần điều kiện `deleted_at`.

### A3 — Chống false negative do post-filter của HNSW (lỗi nghiêm trọng nhất với FR-015)
HNSW trả `ef_search` ứng viên **rồi** mới áp filter (`source_types`, `container`,
`updated_after`), nên một filter hẹp có thể cho 0 dòng dù tồn tại chunk rất giống → tool trả
`status=empty` → Claude tuyên bố "không có dữ liệu index" cho nội dung **thực sự có**. Đây là
false negative được trình bày như sự thật, tức hỏng đúng FR-015. Bắt buộc:
- pgvector ≥ 0.8 và trong transaction read-only: `SET LOCAL hnsw.iterative_scan = relaxed_order`,
  `SET LOCAL hnsw.ef_search = GREATEST(64, 8 * $top_k)`; nếu không có 0.8 thì over-fetch
  `top_k × 4` rồi filter phía ứng dụng.
- `status=empty` **chỉ** khi similarity tốt nhất *không tính filter* dưới `min_similarity`.
- Nếu không-filter có match mà filter làm rỗng ⇒ vẫn `empty` nhưng warning phải nói rõ **bộ
  lọc** loại kết quả.
- Recall kỳ vọng ghi vào đây để QA có mốc test: ≥ 0.95 so với brute-force `SET enable_indexscan=off`
  trên bộ 50 truy vấn mẫu. **Đây là gate *ANN-vs-brute-force correctness* (chứng minh HNSW không bỏ
  sót hàng mà brute-force tìm thấy — chống false negative), KHÔNG phải phép đo chất lượng ngữ nghĩa
  NFR-003.** NFR-003 (truy hồi tìm đúng chunk cho câu hỏi thật) chỉ đo được bằng review tay trên bộ
  mẫu với model embedding thật (ADR-0010) và vẫn UNVERIFIED cho tới khi chốt model đó.

### A4 — Bảng mới `kb.ingest_failures` (chống mất dữ liệu âm thầm)
```sql
CREATE TABLE kb.ingest_failures (
  source_type text NOT NULL, source_id text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(), last_attempt_at timestamptz,
  attempts int NOT NULL DEFAULT 1, stage text, code text, last_error text,
  PRIMARY KEY (source_type, source_id)
);
```
Document fail vĩnh viễn trở nên **nhìn thấy được** (và lấy lại được bằng
`mcp-ingest run --retry-failed`) thay vì bị checkpoint vượt qua rồi biến mất (xem ADR-0012 A2).

### A5 — Migration và retention
Thêm `0006_review_followup.sql` cho A1/A2/A4. Bổ sung lệnh `mcp-ingest prune` (ADR-0012 A5):
không có nó thì `kb` chỉ tăng — bia mộ không bao giờ được dọn và không có bound retention nào.

## Amendments (reconcile contract_issue từ squad-backend, 2026-10-01)

### A6 — `db upgrade` chạy bằng DSN quản trị riêng, không bằng `mcp_ingest_rw`
Migration `0001_extensions.sql` (`CREATE EXTENSION vector, pgcrypto`) và `0005_roles.sql`
(`CREATE ROLE mcp_ingest_rw / mcp_query_ro`) đòi quyền mà `mcp_ingest_rw` (chỉ DML trên `kb`)
**không có và không được có**. Quyết định:
- `mcp-ingest db upgrade` dùng **`MCP_INGEST_ADMIN_DSN`** (role sở hữu schema `kb`, có quyền
  `CREATE` trên database + `CREATEROLE`, hoặc superuser trên dev); **fallback**
  `MCP_INGEST_PGVECTOR_DSN` chỉ khi người vận hành đã cấp quyền DDL cho chính role đó (môi
  trường dev). Thiếu cả hai ⇒ lỗi cấu hình tường minh.
- `MCP_INGEST_ADMIN_DSN` **chỉ** được dùng bởi `db upgrade`; `run`/`reembed`/`prune`/`status`/
  `sources` luôn dùng `MCP_INGEST_PGVECTOR_DSN` (`mcp_ingest_rw`). Không MCP server nào đọc biến
  này; nó không bao giờ được đặt vào `claude_desktop_config.json`.
- Contract `ingest_db_upgrade.x-side-effects` đã cập nhật tương ứng.
