"""mcp-knowledge settings (`MCP_KNOWLEDGE_*`, ADR-0020/0003/0010).

The DSN is the READ-ONLY role ``mcp_query_ro``; the write-capable ``mcp_ingest_rw`` DSN is refused
at startup (ADR-0003 A1, mirrored from :mod:`mcp_pgvector.settings`). The embedding provider/model
are shared with the ingest pipeline through ``MCP_INGEST_EMBEDDING_*`` (ADR-0010): the query must be
embedded in the SAME space as the stored chunks or the server refuses to serve.

The reranker is local + offline (ADR-0020, NFR-011); ``reranker_enabled`` is the explicit on/off and
a missing-weights load produces the same transparent RRF-only fallback (see
:mod:`mcp_knowledge.rerank.local`).
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
        env_prefix="MCP_KNOWLEDGE_", case_sensitive=False, extra="ignore"
    )

    # DSN of the READ-ONLY role `mcp_query_ro`. The write-capable `mcp_ingest_rw` DSN is refused at
    # startup (ADR-0003 A1). The password belongs in the DSN env var / `_FILE`, never in git.
    dsn: SecretStr
    # Optional override of the shared MCP_INGEST_EMBEDDING_MODEL, for this server only. A model that
    # differs from the stored chunks makes the server refuse to serve (re-embed first, ADR-0010).
    embedding_model: str | None = None
    # Local cross-encoder reranker (bge-reranker-v2-m3, loaded OFFLINE). Absence => RRF-only
    # fallback, reported transparently as `reranker=disabled` (L-002); never an error.
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

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
