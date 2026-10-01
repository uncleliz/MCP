-- 0003: indexes (T-056). HNSW cosine (ADR-0011): m=16, ef_construction=64.
-- R12: on a populated table build this with a session-level `maintenance_work_mem`; on the empty
-- table `db upgrade` normally sees it is instant.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON kb.chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON kb.chunks (document_id);
CREATE INDEX IF NOT EXISTS documents_source_updated_idx
    ON kb.documents (source_type, source_updated_at);
