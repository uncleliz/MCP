"""Embedding configuration shared by `mcp-ingest` and `mcp-pgvector` (`MCP_INGEST_EMBEDDING_*`).

One configuration, two consumers: the pipeline embeds documents and the query server embeds the
question in the *same* vector space (ADR-0010). No psycopg import here (R19).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_MODEL", "EmbeddingSettings", "default_max_input_tokens", "prefixes_for"]

# PROVISIONAL (spike S2 / T-054 could not download weights in the squad container; see
# docs/spikes/S2-embedding-bakeoff.md). Both candidates are 1024-d, so switching never needs a
# schema migration, only `mcp-ingest reembed`.
DEFAULT_MODEL = "BAAI/bge-m3"

# (query_prefix, document_prefix) — E5 models are trained with these and degrade without them.
_E5_PREFIXES = ("query: ", "passage: ")


def prefixes_for(model_id: str) -> tuple[str, str]:
    return _E5_PREFIXES if "e5" in model_id.lower() else ("", "")


def default_max_input_tokens(model_id: str) -> int:
    lowered = model_id.lower()
    if "bge-m3" in lowered:
        return 8192
    return 512  # multilingual-e5-large and the usual BERT-sized default


class EmbeddingSettings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_INGEST_EMBEDDING_", case_sensitive=False, extra="ignore"
    )

    provider: Literal["local", "http"] = "local"
    model: str = DEFAULT_MODEL
    dimensions: int = 1024
    normalize: bool = True
    # None = model default (bge-m3 8192, e5 512). Lower values cap CPU time/RAM per chunk.
    max_input_tokens: int | None = None
    batch_size: int = 16
    device: str = "cpu"
    # provider=http: OpenAI-compatible base URL (".../v1"), requests go to "<url>/embeddings".
    url: str | None = None
    api_key: SecretStr | None = None
    timeout_s: float = 30.0
    max_retries: int = 2

    @property
    def resolved_max_input_tokens(self) -> int:
        return self.max_input_tokens or default_max_input_tokens(self.model)
