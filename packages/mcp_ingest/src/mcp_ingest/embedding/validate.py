"""Model/dimension consistency check between configuration and stored data (ADR-0010)."""

from __future__ import annotations

from mcp_ingest.embedding.errors import EmbeddingModelMismatchError
from mcp_ingest.ports import EmbeddingProvider

__all__ = ["validate_stored_embeddings"]


def validate_stored_embeddings(
    provider: EmbeddingProvider, *, stored_models: set[str], stored_dimensions: int | None
) -> None:
    """Raise unless every stored chunk was embedded by exactly the configured model.

    An empty store passes (nothing to be inconsistent with). A *mixed* store (a re-embed that
    was interrupted) fails too: comparing vectors from two spaces silently returns garbage.
    """
    if stored_dimensions is not None and stored_dimensions != provider.dimensions:
        raise EmbeddingModelMismatchError(
            f"configured embedding dimension {provider.dimensions} differs from the "
            f"kb.chunks.embedding column dimension {stored_dimensions}; "
            "change the model back or migrate the column and run `mcp-ingest reembed`"
        )
    if stored_models and stored_models != {provider.model_id}:
        raise EmbeddingModelMismatchError(
            f"configured embedding model '{provider.model_id}' differs from the model(s) stored "
            f"in kb.chunks: {', '.join(sorted(stored_models))}; "
            f"run `mcp-ingest reembed --model {provider.model_id}` to re-embed the corpus first"
        )
