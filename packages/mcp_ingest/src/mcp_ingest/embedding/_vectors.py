"""Small vector helpers shared by the adapters (pure Python, no numpy requirement)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from mcp_ingest.embedding.errors import EmbeddingError

__all__ = ["check_shape", "l2_normalize"]


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return [float(x) for x in vector]
    return [float(x) / norm for x in vector]


def check_shape(vectors: Iterable[Sequence[float]], *, expected: int, count: int) -> None:
    materialised = list(vectors)
    if len(materialised) != count:
        raise EmbeddingError(f"provider returned {len(materialised)} vectors for {count} inputs")
    for vector in materialised:
        if len(vector) != expected:
            raise EmbeddingError(
                f"provider returned a vector of dimension {len(vector)}, expected {expected}"
            )
