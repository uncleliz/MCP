"""mcp-ingest settings (`MCP_INGEST_*`). Embedding settings live in `mcp_ingest.embedding.config`
(prefix `MCP_INGEST_EMBEDDING_`) so that `mcp-pgvector` can share them without importing this
module (which, through the DSNs, is part of the write path).

DSN discipline (ADR-0011 A6, ADR-0003 A1): the DDL-capable `admin_dsn` is read by
:meth:`Settings.migration_dsn` and by nothing else, so only `db upgrade` ever sees it; every
other command uses :meth:`Settings.runtime_dsn` (role `mcp_ingest_rw`).
"""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceMisconfiguredError, SourceSettingsBase
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_GITLAB_FILE_GLOBS", "Settings", "split_csv"]

DEFAULT_GITLAB_FILE_GLOBS = "*.md,*.rst,*.txt,*.adoc"


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_INGEST_", case_sensitive=False, extra="ignore"
    )

    # DDL-capable DSN (CREATE EXTENSION / CREATE ROLE need more than mcp_ingest_rw has); used only
    # by `db upgrade`. Falls back to `pgvector_dsn` when the same role is allowed to do DDL.
    admin_dsn: SecretStr | None = None
    # Write-capable DSN of role `mcp_ingest_rw` — never give this to an MCP server (ADR-0003 A1).
    pgvector_dsn: SecretStr | None = None
    max_doc_retries: int = 2

    # -- chunker (T-074); a change makes `chunk_config_hash` change => documents are re-chunked --
    chunk_tokens: int = 256
    chunk_overlap: int = 32

    # -- Confluence (S5): crawl only the spaces the operator declared team-wide ----------------
    confluence_team_spaces: str = ""
    # -- GitLab (S5) -----------------------------------------------------------------------------
    gitlab_team_projects: str = ""  # path prefixes whose *private* projects count as team
    gitlab_projects: str = ""  # projects to crawl (path_with_namespace or numeric id)
    gitlab_internal_is_team: bool = True
    gitlab_file_globs: str = DEFAULT_GITLAB_FILE_GLOBS
    gitlab_max_file_bytes: int = 262144
    gitlab_include_mrs_issues: bool = True
    # -- OpenSearch: comma-separated allowlist of index aliases; empty = connector disabled -----
    opensearch_indices: str = ""
    opensearch_text_field: str = "content"
    opensearch_title_field: str = "title"
    opensearch_timestamp_field: str = "@timestamp"
    # -- Jira (ADR-0019): project keys to crawl + the subset declared team-wide (default-deny) --
    jira_projects: str = ""  # project keys to crawl (e.g. "PAY,CORE"); empty = connector off
    jira_team_projects: str = ""  # subset of jira_projects whose issues count as team content
    jira_page_size: int = 100

    def migration_dsn(self) -> str:
        chosen = self.admin_dsn or self.pgvector_dsn
        if chosen is None:
            raise SourceMisconfiguredError(
                ["MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"], source="ingest"
            )
        return chosen.get_secret_value()

    def runtime_dsn(self) -> str:
        """DSN of `mcp_ingest_rw` for every command except `db upgrade`."""
        if self.pgvector_dsn is None:
            raise SourceMisconfiguredError(["MCP_INGEST_PGVECTOR_DSN"], source="ingest")
        return self.pgvector_dsn.get_secret_value()
