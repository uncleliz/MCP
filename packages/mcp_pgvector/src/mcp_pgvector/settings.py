"""mcp-pgvector settings (`MCP_PGVECTOR_*`). The embedding provider/model are shared with the
ingest pipeline through `MCP_INGEST_EMBEDDING_*` (ADR-0010): both must embed in the same space.
"""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from mcp_ingest.embedding import EmbeddingSettings
from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["Settings"]


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_PGVECTOR_", case_sensitive=False, extra="ignore"
    )

    # DSN of the READ-ONLY role `mcp_query_ro`. The write-capable `mcp_ingest_rw` DSN is refused
    # at startup (ADR-0003 A1). The password belongs in the DSN env var / `_FILE`, never in git.
    dsn: SecretStr
    # Optional override of the shared MCP_INGEST_EMBEDDING_MODEL, for this server only. A model
    # that differs from the stored chunks makes the server refuse to serve (re-embed first).
    embedding_model: str | None = None

    @field_validator("dsn")
    @classmethod
    def _validate_dsn(cls, value: SecretStr) -> SecretStr:
        text = value.get_secret_value().strip()
        if not text:
            raise ValueError("must not be empty")
        return SecretStr(text)

    def embedding_settings(self) -> EmbeddingSettings:
        settings = EmbeddingSettings()
        if self.embedding_model:
            settings = settings.model_copy(update={"model": self.embedding_model})
        return settings
