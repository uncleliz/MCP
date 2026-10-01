"""Ports of the ingest pipeline (ADR-0010).

Import-light on purpose: this module is imported by the read-only `mcp-pgvector` server, so
it must never import `psycopg` or any write path (R19; `tests/test_import_boundary.py`).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["EmbeddingProvider"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors of a fixed dimension, in the same space for documents and queries.

    The methods are synchronous (the pipeline is a CLI); async callers such as `mcp-pgvector`
    run them on a bounded executor. Vectors are L2-normalised when `normalize` is true, so that
    pgvector's cosine distance and inner product agree.
    """

    @property
    def model_id(self) -> str:
        """Stored in `kb.chunks.embedding_model`; a mismatch with stored data forces re-embed."""
        ...

    @property
    def dimensions(self) -> int: ...

    @property
    def max_input_tokens(self) -> int: ...

    @property
    def normalize(self) -> bool: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
