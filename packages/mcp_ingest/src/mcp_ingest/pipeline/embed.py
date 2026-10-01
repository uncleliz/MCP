"""Stage `embed` (T-075): chunks -> vectors in batches through the `EmbeddingProvider` port."""

from __future__ import annotations

from mcp_ingest.pipeline.chunk import Chunk, embed_input
from mcp_ingest.ports import EmbeddingProvider

__all__ = ["embed_chunks", "vector_literal"]


def vector_literal(vector: list[float]) -> str:
    """pgvector text input format, cast with `%s::vector` (no client-side pgvector package)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def embed_texts(
    provider: EmbeddingProvider, texts: list[str], batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), max(1, batch_size)):
        batch = provider.embed_documents(texts[start : start + batch_size])
        if len(batch) != len(texts[start : start + batch_size]):
            raise ValueError("embedding provider returned a different number of vectors")
        vectors.extend(batch)
    for vector in vectors:
        if len(vector) != provider.dimensions:
            raise ValueError(
                f"embedding provider returned {len(vector)} dimensions, "
                f"expected {provider.dimensions}"
            )
    return vectors


def embed_chunks(
    provider: EmbeddingProvider, chunks: list[Chunk], batch_size: int
) -> list[list[float]]:
    return embed_texts(
        provider, [embed_input(c.heading_path, c.content) for c in chunks], batch_size
    )
