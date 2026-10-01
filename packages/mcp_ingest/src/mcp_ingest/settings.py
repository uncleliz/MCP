"""mcp-ingest settings (`MCP_INGEST_*`). Embedding settings live in `mcp_ingest.embedding.config`
(prefix `MCP_INGEST_EMBEDDING_`) so that `mcp-pgvector` can share them without importing this
module (which, through the DSNs, is part of the write path).
"""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceMisconfiguredError, SourceSettingsBase
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

__all__ = ["Settings"]


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
    # Comma-separated allowlist of index patterns; empty = connector disabled (ADR-0012 A5).
    opensearch_indices: str = ""

    def migration_dsn(self) -> str:
        chosen = self.admin_dsn or self.pgvector_dsn
        if chosen is None:
            raise SourceMisconfiguredError(
                ["MCP_INGEST_ADMIN_DSN", "MCP_INGEST_PGVECTOR_DSN"], source="ingest"
            )
        return chosen.get_secret_value()
