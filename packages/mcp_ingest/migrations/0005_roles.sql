-- 0005: roles (T-056, ADR-0003 A1/A4). Passwords are NOT set here (never in a committed file):
-- the operator runs `ALTER ROLE ... PASSWORD` out of band (see infra/dev-setup.md).
--   mcp_ingest_rw : DML on kb for the ingest pipeline only (never given to an MCP server).
--   mcp_query_ro  : SELECT only, read-only by default; the credential of mcp-pgvector.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp_ingest_rw') THEN
        CREATE ROLE mcp_ingest_rw LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp_query_ro') THEN
        CREATE ROLE mcp_query_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER ROLE mcp_query_ro SET default_transaction_read_only = on;
ALTER ROLE mcp_query_ro SET statement_timeout = '15s';

REVOKE ALL ON SCHEMA kb FROM PUBLIC;
GRANT USAGE ON SCHEMA kb TO mcp_ingest_rw, mcp_query_ro;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON kb.documents, kb.chunks, kb.ingest_runs, kb.ingest_source_state TO mcp_ingest_rw;
GRANT SELECT ON kb.schema_migrations TO mcp_ingest_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA kb TO mcp_ingest_rw;

GRANT SELECT
    ON kb.documents, kb.chunks, kb.ingest_runs, kb.ingest_source_state, kb.schema_migrations
    TO mcp_query_ro;
