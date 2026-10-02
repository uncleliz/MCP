-- 0007: CHG-001 E1 — four knowledge domains in schema kb (T-088, ADR-0022, ADR-0017 Option C).
--
-- Runs on the pgvector store that is ALREADY go-live with data (DK2 / R-006/R-007). This file is
-- transactional and only ever CREATEs new objects or ADDs columns/constraints that do NOT rewrite
-- or long-lock the populated kb.documents / kb.chunks tables:
--   * every table here is brand-new (empty) -> CREATE TABLE / its own indexes take no live lock;
--   * the one touch to a populated table is kb.chunks.content_tsv, added with a CONSTANT default
--     (NULL) so Postgres >= 11 does NOT rewrite the table; it is backfilled + indexed in the
--     separate CONCURRENTLY migration 0007b (outside any transaction);
--   * FK/CHECK constraints on new tables validate instantly (the tables are empty), so they are
--     written inline; the DK2 "ADD CONSTRAINT ... NOT VALID then VALIDATE separately" dance is for
--     constraints added to POPULATED tables and is demonstrated on kb.chunks below.
-- No ALTER COLUMN ... TYPE anywhere (the runner refuses it on populated data).

-- -- document versioning (minimal chain; full obsolete-propagation chain is B7 backlog) ----------
CREATE TABLE IF NOT EXISTS kb.document_versions (
    id             bigserial   PRIMARY KEY,
    document_id    uuid        NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    version        integer     NOT NULL,                 -- monotonic per document, 1-based
    content_hash   text        NOT NULL,                 -- sha256 of the normalised content
    source_version text,                                 -- upstream version/revision if any
    status         text        NOT NULL DEFAULT 'current'
        CHECK (status IN ('current', 'superseded')),
    author         text,
    source_updated_at timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_versions_doc_version_uk UNIQUE (document_id, version)
);

-- -- entities + relationships (business graph; shallow 2-3 hop recursive CTE, ADR-0022 DP4) ------
CREATE TABLE IF NOT EXISTS kb.entities (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type text        NOT NULL
        CHECK (entity_type IN ('service', 'repository', 'team', 'document', 'component', 'topic')),
    name        text        NOT NULL,                    -- canonical name within its type
    display_name text,
    document_id uuid        REFERENCES kb.documents (id) ON DELETE SET NULL,  -- backing doc if any
    metadata    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entities_type_name_uk UNIQUE (entity_type, name)
);

CREATE TABLE IF NOT EXISTS kb.relationships (
    id            bigserial   PRIMARY KEY,
    src_entity_id uuid        NOT NULL REFERENCES kb.entities (id) ON DELETE CASCADE,
    dst_entity_id uuid        NOT NULL REFERENCES kb.entities (id) ON DELETE CASCADE,
    rel_type      text        NOT NULL
        CHECK (rel_type IN ('depends_on', 'documented_by', 'owns', 'related_to')),
    metadata      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relationships_edge_uk UNIQUE (src_entity_id, dst_entity_id, rel_type),
    CONSTRAINT relationships_no_self_loop_ck CHECK (src_entity_id <> dst_entity_id)
);

-- -- knowledge summaries (filled at ingest; read-only on the Live path) --------------------------
CREATE TABLE IF NOT EXISTS kb.knowledge_summaries (
    id           bigserial   PRIMARY KEY,
    subject_type text        NOT NULL
        CHECK (subject_type IN ('entity', 'topic')),
    subject_id   text        NOT NULL,                   -- entity id (uuid as text) or topic key
    summary      text        NOT NULL,
    provenance   jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- array of {document_id, chunk_id, uri}
    generated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_summaries_subject_uk UNIQUE (subject_type, subject_id)
);

-- -- document permissions (normalised visibility -> (document, principal, grant), default-deny) --
-- Foundation for enforce_permission (ADR-0021) run BEFORE context assembly. Absence of a row =
-- deny (default-deny); 'team' broadcasts to the pseudo-principal '*team*' as a migration seam.
CREATE TABLE IF NOT EXISTS kb.document_permissions (
    id          bigserial   PRIMARY KEY,
    document_id uuid        NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    principal   text        NOT NULL,                    -- user/group id, or '*team*' for team-wide
    grant_type  text        NOT NULL DEFAULT 'read'
        CHECK (grant_type IN ('read')),                  -- read-only product; no write grant exists
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_permissions_doc_principal_grant_uk UNIQUE (document_id, principal, grant_type)
);

-- -- populated-table change: kb.chunks full-text column (DK2 — constant default, no rewrite) -----
-- Added here with a CONSTANT default (NULL) so Postgres does not rewrite the populated table; the
-- backfill UPDATE and the GIN index live in 0007b (CONCURRENTLY, outside a transaction).
ALTER TABLE kb.chunks
    ADD COLUMN IF NOT EXISTS content_tsv tsvector;

-- A CHECK on a POPULATED table, written the DK2-safe way: ADD ... NOT VALID (a brief lock that
-- does NOT scan existing rows) then VALIDATE in a SEPARATE statement (SHARE UPDATE EXCLUSIVE — it
-- does not block reads or writes). token_count, when present, must be non-negative.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'chunks_token_count_ck') THEN
        ALTER TABLE kb.chunks
            ADD CONSTRAINT chunks_token_count_ck CHECK (token_count IS NULL OR token_count >= 0)
            NOT VALID;
    END IF;
END
$$;

ALTER TABLE kb.chunks VALIDATE CONSTRAINT chunks_token_count_ck;

-- -- grants: Live path reads via mcp_query_ro; ingest writes via mcp_ingest_rw (DK3) -------------
GRANT SELECT
    ON kb.document_versions, kb.entities, kb.relationships, kb.knowledge_summaries,
       kb.document_permissions
    TO mcp_query_ro;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON kb.document_versions, kb.entities, kb.relationships, kb.knowledge_summaries,
       kb.document_permissions
    TO mcp_ingest_rw;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA kb TO mcp_ingest_rw;
