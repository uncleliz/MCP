-- 0008: CHG-001 E1 — source-authority config + confidence weights + freshness horizons (T-088,
-- ADR-0022, ADR-0018 §4, spec §42). Config lives in the DB (NOT hardcoded in any prompt): the
-- grounding gate reads these rows to pick authority_note by fact type, to weight the deterministic
-- confidence factors, and to decide when a fact is stale. All new empty tables — no live lock.

-- Which source is authoritative for each kind of fact (used for CONFLICT authority_note).
CREATE TABLE IF NOT EXISTS kb.source_authority (
    fact_type           text        PRIMARY KEY,       -- e.g. runtime_config, architecture, ...
    authoritative_source text       NOT NULL,          -- confluence | gitlab | jira | opensearch
    rationale           text        NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Deterministic confidence weights (ADR-0018 D1): confidence = retrieval^w_r * agreement^w_a *
-- freshness^w_f (geometric). Default 0.5 / 0.3 / 0.2. One row ('default'); per-fact-type overrides
-- may be added later without a migration.
CREATE TABLE IF NOT EXISTS kb.confidence_weights (
    profile    text         PRIMARY KEY,
    w_retrieval  numeric(4, 3) NOT NULL CHECK (w_retrieval  >= 0 AND w_retrieval  <= 1),
    w_agreement  numeric(4, 3) NOT NULL CHECK (w_agreement  >= 0 AND w_agreement  <= 1),
    w_freshness  numeric(4, 3) NOT NULL CHECK (w_freshness  >= 0 AND w_freshness  <= 1),
    updated_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT confidence_weights_sum_to_one_ck
        CHECK (round(w_retrieval + w_agreement + w_freshness, 3) = 1.000)
);

-- How long a fact of a given type stays "fresh" before its freshness factor is reduced (NOT made
-- wrong — ADR-0018 §4). Hours; NULL = never considered stale.
CREATE TABLE IF NOT EXISTS kb.freshness_horizon (
    fact_type        text        PRIMARY KEY,
    horizon_hours    integer     CHECK (horizon_hours IS NULL OR horizon_hours > 0),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- -- seed: source authority by fact type (architecture.md "Live-vs-Knowledge & freshness") -------
INSERT INTO kb.source_authority (fact_type, authoritative_source, rationale) VALUES
    ('runtime_config',      'gitlab',     'Runtime/config values live in the repo (GitLab).'),
    ('architecture',        'confluence', 'Architecture/design docs are authored in Confluence.'),
    ('current_work_status', 'jira',       'Live work status (sprint/issue) is authoritative in Jira.'),
    ('code_behavior',       'gitlab',     'Code behaviour is defined by the source in GitLab.')
ON CONFLICT (fact_type) DO NOTHING;

-- -- seed: default confidence weights 0.5 / 0.3 / 0.2 (ADR-0018 D1) ------------------------------
INSERT INTO kb.confidence_weights (profile, w_retrieval, w_agreement, w_freshness) VALUES
    ('default', 0.500, 0.300, 0.200)
ON CONFLICT (profile) DO NOTHING;

-- -- seed: freshness horizons by fact type ------------------------------------------------------
INSERT INTO kb.freshness_horizon (fact_type, horizon_hours) VALUES
    ('runtime_config',      168),   -- 7 days
    ('architecture',        2160),  -- 90 days
    ('current_work_status', 24),    -- 1 day (live work changes fast)
    ('code_behavior',       720)    -- 30 days
ON CONFLICT (fact_type) DO NOTHING;

-- -- grants: config is read on the Live path; written by ingest/ops via mcp_ingest_rw -----------
GRANT SELECT
    ON kb.source_authority, kb.confidence_weights, kb.freshness_horizon
    TO mcp_query_ro;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON kb.source_authority, kb.confidence_weights, kb.freshness_horizon
    TO mcp_ingest_rw;
