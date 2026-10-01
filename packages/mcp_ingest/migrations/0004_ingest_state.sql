-- 0004: run bookkeeping (T-056). One kb.ingest_runs row per source per run (ADR-0012 A3).
-- kb.schema_migrations is bootstrapped by the runner itself (it must exist before 0001); the
-- IF NOT EXISTS here only documents it as part of the schema.
CREATE TABLE IF NOT EXISTS kb.schema_migrations (
    version    text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb.ingest_runs (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type        text        NOT NULL,
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    status             text        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    documents_seen     integer     NOT NULL DEFAULT 0,
    documents_upserted integer     NOT NULL DEFAULT 0,
    documents_skipped  integer     NOT NULL DEFAULT 0,
    documents_failed   integer     NOT NULL DEFAULT 0,
    chunks_written     integer     NOT NULL DEFAULT 0,
    error_summary      jsonb
);
CREATE INDEX IF NOT EXISTS ingest_runs_source_started_idx
    ON kb.ingest_runs (source_type, started_at DESC);

CREATE TABLE IF NOT EXISTS kb.ingest_source_state (
    source_type     text        PRIMARY KEY,
    cursor          jsonb,                    -- incremental watermark (inclusive >=, ADR-0012 A2)
    last_success_at timestamptz,
    last_run_id     uuid        REFERENCES kb.ingest_runs (id) ON DELETE SET NULL
);
