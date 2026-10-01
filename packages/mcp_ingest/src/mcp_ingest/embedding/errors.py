"""Embedding errors (no psycopg import, R19)."""

from __future__ import annotations

__all__ = ["EmbeddingError", "EmbeddingModelMismatchError"]


class EmbeddingError(RuntimeError):
    """The provider could not produce vectors of the declared shape."""


class EmbeddingModelMismatchError(EmbeddingError):
    """Configured model/dimension differs from what is stored in `kb.chunks` (ADR-0010)."""
