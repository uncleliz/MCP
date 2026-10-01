"""Deterministic fake provider: hashed bag-of-words vectors. For tests, eval smoke runs and the
bake-off script's dry run — never a production provider (no semantics beyond token overlap).
"""

from __future__ import annotations

import hashlib
import re

from mcp_ingest.embedding._vectors import l2_normalize

__all__ = ["DeterministicFakeProvider"]

_TOKEN = re.compile(r"\w+", re.UNICODE)


class DeterministicFakeProvider:
    def __init__(
        self,
        *,
        dimensions: int = 1024,
        model_id: str = "fake/hashed-bow",
        max_input_tokens: int = 512,
        normalize: bool = True,
    ) -> None:
        self._dimensions = dimensions
        self._model_id = model_id
        self._max_input_tokens = max_input_tokens
        self._normalize = normalize

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def max_input_tokens(self) -> int:
        return self._max_input_tokens

    @property
    def normalize(self) -> bool:
        return self._normalize

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        if not any(vector):
            vector[0] = 1.0
        return l2_normalize(vector) if self._normalize else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
