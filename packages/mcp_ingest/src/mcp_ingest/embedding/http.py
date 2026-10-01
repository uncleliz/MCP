"""`HttpEmbeddingProvider` — OpenAI-compatible `POST <url>/embeddings` (ADR-0010).

Works with OpenAI, Voyage, a self-hosted TEI/vLLM. The API key is a `SecretStr` and is never
placed in an error message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from mcp_ingest.embedding._vectors import check_shape, l2_normalize
from mcp_ingest.embedding.config import EmbeddingSettings, prefixes_for
from mcp_ingest.embedding.errors import EmbeddingError

__all__ = ["HttpEmbeddingProvider"]

_RETRYABLE = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpEmbeddingProvider:
    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.url:
            raise EmbeddingError("provider=http requires MCP_INGEST_EMBEDDING_URL")
        self._settings = settings
        self._endpoint = settings.url.rstrip("/") + "/embeddings"
        self._sleep = sleep
        self._query_prefix, self._document_prefix = prefixes_for(settings.model)
        headers = {"Content-Type": "application/json"}
        if settings.api_key is not None:
            headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"
        self._client = httpx.Client(
            headers=headers, timeout=settings.timeout_s, transport=transport
        )

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

    def close(self) -> None:
        self._client.close()

    def _post(self, inputs: list[str]) -> list[list[float]]:
        payload = {"model": self._settings.model, "input": inputs}
        attempts = self._settings.max_retries + 1
        last = "unknown error"
        for attempt in range(attempts):
            if attempt:
                self._sleep(min(2.0**attempt, 8.0))
            try:
                response = self._client.post(self._endpoint, json=payload)
            except httpx.TransportError as exc:
                last = f"embedding endpoint unreachable ({type(exc).__name__})"
                continue
            if response.status_code in _RETRYABLE:
                last = f"embedding endpoint returned HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                raise EmbeddingError(
                    f"embedding endpoint returned HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
            return self._parse(response.json(), count=len(inputs))
        raise EmbeddingError(f"{last} after {attempts} attempts")

    def _parse(self, body: Any, *, count: int) -> list[list[float]]:
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise EmbeddingError("embedding endpoint returned no 'data' list")
        ordered = sorted(data, key=lambda row: row.get("index", 0))
        vectors = [[float(x) for x in row["embedding"]] for row in ordered]
        check_shape(vectors, expected=self.dimensions, count=count)
        return [l2_normalize(v) for v in vectors] if self.normalize else vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        size = max(self._settings.batch_size, 1)
        for start in range(0, len(texts), size):
            batch = [self._document_prefix + t for t in texts[start : start + size]]
            out.extend(self._post(batch))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._post([self._query_prefix + text])[0]
