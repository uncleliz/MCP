-- 0006: design-review follow-up (T-057, ADR-0011 A1/A4/A5, ADR-0016).
ALTER TABLE kb.documents
    ADD COLUMN IF NOT EXISTS last_seen_run_id uuid REFERENCES kb.ingest_runs (id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_seen_at timestamptz,
    ADD COLUMN IF NOT EXISTS chunk_config_hash text,
    ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'team';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_constraint WHERE conname = 'documents_visibility_ck'
    ) THEN
        ALTER TABLE kb.documents
            ADD CONSTRAINT documents_visibility_ck CHECK (visibility IN ('team', 'restricted'));
    END IF;
END
$$;

-- Makes the scoped tombstone statement of ADR-0011 A1 cheap:
--   UPDATE kb.documents SET deleted_at = now()
--    WHERE source_type = $1 AND last_seen_run_id IS DISTINCT FROM $2 AND deleted_at IS NULL
CREATE INDEX IF NOT EXISTS documents_source_last_seen_idx
    ON kb.documents (source_type, last_seen_run_id);

CREATE TABLE IF NOT EXISTS kb.ingest_failures (
    source_type     text        NOT NULL,
    source_id       text        NOT NULL,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz NOT NULL DEFAULT now(),
    attempts        integer     NOT NULL DEFAULT 1,
    stage           text        NOT NULL
        CHECK (stage IN ('config', 'connect', 'crawl', 'normalize', 'redact', 'chunk', 'embed',
                         'persist', 'reconcile', 'prune')),
    code            text        NOT NULL,     -- blocked_by_policy | upstream_* | ...
    last_error      text,
    PRIMARY KEY (source_type, source_id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON kb.ingest_failures TO mcp_ingest_rw;
GRANT SELECT ON kb.ingest_failures TO mcp_query_ro;
