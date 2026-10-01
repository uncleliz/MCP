-- 0002: schema kb + documents + chunks (T-056, architecture.md "Data model", ADR-0011).
-- Columns added later by 0006 (last_seen_*, chunk_config_hash, visibility) are deliberately
-- NOT here, so the migration history matches the review follow-up (ADR-0011 A5).
-- vector(1024): both S2 candidates (bge-m3, multilingual-e5-large) are 1024-d, so the model can
-- change without a migration (only `mcp-ingest reembed`).
CREATE SCHEMA IF NOT EXISTS kb;

CREATE TABLE IF NOT EXISTS kb.documents (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type       text        NOT NULL,
    source_id         text        NOT NULL,
    source_uri        text        NOT NULL,   -- original URL, used as citation (BR-005)
    title             text,
    container         text,                   -- space key / project path
    author            text,
    content_hash      text        NOT NULL,   -- sha256 of the normalised content (FR-012/AC-003)
    source_updated_at timestamptz,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,            -- tombstone after a full reconcile
    metadata          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT documents_source_uk UNIQUE (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS kb.chunks (
    id              bigserial   PRIMARY KEY,
    document_id     uuid        NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    chunk_index     integer     NOT NULL,
    content         text        NOT NULL,     -- redacted but NOT wrapped (ADR-0015 A2)
    token_count     integer,
    heading_path    text,
    embedding       vector(1024) NOT NULL,
    embedding_model text        NOT NULL,
    embedded_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chunks_document_index_uk UNIQUE (document_id, chunk_index)
);
