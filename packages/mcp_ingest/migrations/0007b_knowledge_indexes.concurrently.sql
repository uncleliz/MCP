-- 0007b: CHG-001 E1 — indexes built CONCURRENTLY on populated tables (T-088, ADR-0022 DK2).
--
-- The `.concurrently.sql` marker makes the runner execute these statements one at a time in
-- AUTOCOMMIT, OUTSIDE any transaction (R-006/R-007): `CREATE INDEX CONCURRENTLY` is illegal inside
-- a transaction block, and it must not take a long ACCESS EXCLUSIVE lock on the already-populated
-- kb.chunks table. The runner drops any INVALID index a failed build leaves behind and retries
-- once. Every statement is idempotent so a re-run is a no-op.

-- Backfill the full-text column added (as a constant default) in 0007. A plain UPDATE on a small
-- demo corpus is fine; the GIN index below is what must be built concurrently.
UPDATE kb.chunks
   SET content_tsv = to_tsvector('simple', content)
 WHERE content_tsv IS NULL;

-- Full-text search leg of hybrid-RAG (ADR-0020): GIN on the tsvector of kb.chunks.
CREATE INDEX CONCURRENTLY IF NOT EXISTS chunks_content_tsv_gin
    ON kb.chunks USING gin (content_tsv);

-- Relationship traversal (recursive CTE, ADR-0022): btree both directions so a bounded walk can
-- expand from src or dst without a sequential scan.
CREATE INDEX CONCURRENTLY IF NOT EXISTS relationships_src_idx
    ON kb.relationships (src_entity_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS relationships_dst_idx
    ON kb.relationships (dst_entity_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS relationships_rel_type_idx
    ON kb.relationships (rel_type);

-- document_versions lookup by (document_id, version) for get_document_version.
CREATE INDEX CONCURRENTLY IF NOT EXISTS document_versions_doc_version_idx
    ON kb.document_versions (document_id, version);

-- enforce_permission (ADR-0021) filters candidates by document before context assembly.
CREATE INDEX CONCURRENTLY IF NOT EXISTS document_permissions_doc_idx
    ON kb.document_permissions (document_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS document_permissions_principal_idx
    ON kb.document_permissions (principal);

-- entities lookup for find_related_knowledge / get_service / get_repository.
CREATE INDEX CONCURRENTLY IF NOT EXISTS entities_type_name_idx
    ON kb.entities (entity_type, name);
