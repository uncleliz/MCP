# ADR-0022: 4 domain dữ liệu Company Knowledge trong Postgres — versioning, entities/relationships (recursive CTE), summaries, permissions; migration-locking an toàn trên dữ liệu đã có

**Date**: 2026-10-01
**Status**: accepted — CHG-001 Option C (ADR-0017), DP3/DP4 + gánh DK2/DK3
**Deciders**: SA (squad-sa) đề xuất; CTO quyết trong scope CHG-001 Option C

## Context

Option C thêm 4 domain dữ liệu vào **cùng** schema `kb` (không datastore mới): document
versioning (lịch sử/rollback), entities + relationships (graph nghiệp vụ), knowledge summaries,
document permissions. Quan trọng: các migration này chạy trên **pgvector đã go-live có dữ
liệu** (demo-seed + corpus) — nên **DK2** (R-006/R-007 migration-locking) phải được gánh:
`ALTER … ADD CONSTRAINT … NOT VALID` rồi `VALIDATE` riêng; `CREATE INDEX CONCURRENTLY` **ngoài**
transaction per-file. **DK3**: credential của domain mới (nếu có) không tái dùng Redis dev ACL
cho shared. Relationship traversal là graph **nông 2–3 hop** → recursive CTE đủ (DP4), không AGE.

## Decision

**Bốn domain, tất cả trong schema `kb`:**
- `kb.document_versions` — `(document_id, version, content_hash, source_version, created_at, …)`,
  chuỗi version cho rollback/obsolete-propagation (bản tối thiểu; chain đầy đủ chờ B7 backlog).
- `kb.entities` + `kb.relationships` — entity (service/repo/team/doc…) + cạnh có kiểu
  (`depends_on`/`documented_by`/`owns`/`related_to`); traversal bằng **recursive CTE** bounded
  `depth ≤ 3`, `cycle` detection, `LIMIT` fanout.
- `kb.knowledge_summaries` — summary cache theo entity/topic (`subject_type`, `subject_id`,
  `summary`, `provenance[]`, `generated_at`) — read-only ở Live path, điền ở ingest.
- `kb.document_permissions` — chuẩn hoá `visibility` thành `(document_id, principal, grant)`
  default-deny; nền cho `enforce_permission` (ADR-0021) trước context assembly.

**Migration-locking an toàn (DK2 — bắt buộc vì chạy trên dữ liệu đã có):**
- Thêm FK/CHECK: `ADD CONSTRAINT … NOT VALID` (không khoá full-table), rồi `VALIDATE CONSTRAINT`
  ở câu riêng (SHARE UPDATE EXCLUSIVE, không chặn đọc/ghi).
- Tạo index trên bảng đã có dữ liệu: `CREATE INDEX CONCURRENTLY` chạy **ngoài** transaction —
  runner `db upgrade` tách các file `*.concurrently.sql` ra **autocommit**, không gói trong `BEGIN`.
- `ADD COLUMN` dùng default hằng (Postgres ≥ 11 không rewrite); tránh `ALTER COLUMN TYPE` tại chỗ.
- Mỗi migration có `lock_timeout` + `statement_timeout` ngắn để không chờ khoá vô hạn.

**DK3:** domain mới dùng role `mcp_query_ro` để đọc (đã có); nếu cần credential riêng (vd write
summaries ở ingest) thì cấp qua `mcp_ingest_rw`, **không** tái dùng Redis dev ACL broad grants
cho bất kỳ shared env nào (ghi vào env-promotion CHG-001).

## Alternatives Considered

### Alternative 1: Apache AGE / graph DB riêng cho relationships (DP4-B)
- **Pros**: openCypher, traversal sâu.
- **Cons**: Extension/datastore mới = deviation + untyped Cypher + operating burden; graph nông 2–3 hop không cần.
- **Why not**: CTE đủ cho spec §21–22; AGE để dành khi **đo được** CTE chậm trên graph thật.

### Alternative 2: Migration bình thường (gói mọi thứ trong một transaction)
- **Pros**: Đơn giản, atomic.
- **Cons**: `CREATE INDEX` thường + `VALIDATE` khoá full-table trên dữ liệu prod đã có ⇒ chặn đọc/ghi (đúng R-006/R-007 DK2).
- **Why not**: Lần đầu đổi schema trên dữ liệu đã có phải NOT VALID + CONCURRENTLY (DK2 là retro follow-up chặn build).

## Consequences

### Positive
- Bốn năng lực mới trong **một** store; không datastore/extension mới (giữ bất biến "một Postgres").
- Migration an toàn trên dữ liệu đã go-live (không downtime khoá bảng).

### Negative
- `CREATE INDEX CONCURRENTLY` có thể fail để lại index invalid → runner phải phát hiện + `DROP`/retry; không chạy trong transaction nên không rollback tự động.
- Recursive CTE phải bound depth/fanout để không nổ chi phí trên graph dày bất ngờ.

### Risks
- CTE chậm trên graph thật ngoài dự kiến (R: đo ở eval) → đường thoát là AGE (Alternative 1), đổi **chỉ** DP4 không đổi DP1/DP3.
- Nhãn permission sai → giảm thiểu default-deny (ADR-0016 A2) + adversarial test (L-001).

## Links
- ADR-0011 (pgvector schema/upsert/HNSW — DK2 R-006/R-007), ADR-0012 (migration runner CLI), ADR-0016 (visibility/permission default-deny),
  ADR-0017 (CHG-001 Option C — DP3/DP4), ADR-0021 (enforce_permission dùng document_permissions).
  Retro DK2/DK3 (`9-retro/retro.md`, `plan-approval.md`). Spec §21–22.
