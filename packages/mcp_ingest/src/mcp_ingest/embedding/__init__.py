"""Embedding adapters (ADR-0010). Importing this package must not import `psycopg` (R19) nor
load a model: adapters are imported lazily by :func:`build_provider`.
"""

from __future__ import annotations

from mcp_ingest.embedding.config import DEFAULT_MODEL, EmbeddingSettings
from mcp_ingest.embedding.errors import EmbeddingError, EmbeddingModelMismatchError
from mcp_ingest.embedding.validate import validate_stored_embeddings
from mcp_ingest.ports import EmbeddingProvider

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingError",
    "EmbeddingModelMismatchError",
    "EmbeddingProvider",
    "EmbeddingSettings",
    "build_provider",
    "validate_stored_embeddings",
]


def build_provider(settings: EmbeddingSettings | None = None) -> EmbeddingProvider:
    """`MCP_INGEST_EMBEDDING_PROVIDER=local|http` -> adapter (the model is not loaded yet)."""
    settings = settings or EmbeddingSettings()
    if settings.provider == "http":
        from mcp_ingest.embedding.http import HttpEmbeddingProvider

        return HttpEmbeddingProvider(settings)
    from mcp_ingest.embedding.local import LocalSentenceTransformerProvider

    return LocalSentenceTransformerProvider(settings)
