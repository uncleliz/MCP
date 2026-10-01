"""`LocalSentenceTransformerProvider` — in-process sentence-transformers model (ADR-0010).

The model (~2 GB for bge-m3) is loaded lazily, once per process; constructing the provider and
importing this module are free. `sentence_transformers` is an optional dependency
(`mcp-ingest[local-embeddings]`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from mcp_ingest.embedding._vectors import check_shape, l2_normalize
from mcp_ingest.embedding.config import EmbeddingSettings, prefixes_for
from mcp_ingest.embedding.errors import EmbeddingError

__all__ = ["LocalSentenceTransformerProvider"]

ModelFactory = Callable[..., Any]
_CACHE: dict[tuple[str, str], Any] = {}
_CACHE_LOCK = threading.Lock()


def _default_factory(model_id: str, **kwargs: Any) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise EmbeddingError(
            "provider=local needs the optional dependency: "
            "uv sync --package mcp-ingest --extra local-embeddings"
        ) from exc
    return SentenceTransformer(model_id, **kwargs)  # pragma: no cover - downloads weights


class LocalSentenceTransformerProvider:
    def __init__(
        self, settings: EmbeddingSettings, *, model_factory: ModelFactory | None = None
    ) -> None:
        self._settings = settings
        self._factory = model_factory
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._query_prefix, self._document_prefix = prefixes_for(settings.model)

    @property
    def model_id(self) -> str:
        return self._settings.model

    @property
    def dimensions(self) -> int:
        return self._settings.dimensions

    @property
    def max_input_tokens(self) -> int:
        return self._settings.resolved_max_input_tokens

    @property
    def normalize(self) -> bool:
        return self._settings.normalize

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:  # pragma: no cover - race only
                return self._model
            settings = self._settings
            if self._factory is not None:
                model = self._factory(settings.model, device=settings.device)
            else:
                key = (settings.model, settings.device)
                with _CACHE_LOCK:
                    if key not in _CACHE:
                        _CACHE[key] = _default_factory(settings.model, device=settings.device)
                    model = _CACHE[key]
            actual = model.get_sentence_embedding_dimension()
            if actual != settings.dimensions:
                raise EmbeddingError(
                    f"model '{settings.model}' produces dimension {actual}, but "
                    f"MCP_INGEST_EMBEDDING_DIMENSIONS={settings.dimensions}"
                )
            model.max_seq_length = min(
                int(getattr(model, "max_seq_length", self.max_input_tokens) or 0)
                or self.max_input_tokens,
                self.max_input_tokens,
            )
            self._model = model
            return model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        raw = model.encode(
            texts,
            batch_size=self._settings.batch_size,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        vectors = [[float(x) for x in row] for row in raw]
        check_shape(vectors, expected=self.dimensions, count=len(texts))
        return [l2_normalize(v) for v in vectors] if self.normalize else vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._encode([self._document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self._query_prefix + text])[0]
