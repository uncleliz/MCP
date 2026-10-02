"""Local, offline cross-encoder reranker with a transparent RRF-only fallback (T-097, ADR-0020).

The reranker is ``BAAI/bge-reranker-v2-m3`` (Apache-2.0), loaded from **local disk** with
``HF_HUB_OFFLINE=1`` so no HuggingFace egress ever happens (ADR-0020, NFR-011). It is loaded
**lazily, once per process**; importing this module and constructing the reranker are free.

Fallback discipline (L-002 — the lesson this whole task turns on): if the weights are absent or
cannot be loaded, the engine **does not raise and does not silently degrade**. It falls back to the
RRF order (which is already a correct ranking) and reports ``reranker="disabled"`` so the degraded
ranking is never presented as full quality. The flag ``MCP_RAG_RERANKER_ENABLED=false`` is the
explicit way to turn reranking off; a missing-weights fallback produces the same transparent signal.

No outbound socket is opened on any path here: offline load reads the local snapshot, and scoring is
pure CPU/GPU compute. ``tests/test_no_egress_rerank.py`` asserts this.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_RERANKER_MODEL",
    "RerankOutcome",
    "Reranker",
    "RerankerStatus",
]

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# `enabled` / `disabled` match `GroundingSummary.reranker` in the contract.
RerankerStatus = str


@dataclass(frozen=True)
class RerankOutcome:
    """The result of a rerank attempt over a candidate list.

    ``order`` is the indices into the input list, best-first. ``status`` is ``"enabled"`` when the
    cross-encoder actually re-scored, ``"disabled"`` when the engine fell back to the input (RRF)
    order. ``reason`` explains a fallback (for telemetry/logging) and is ``None`` when enabled.
    """

    order: list[int]
    status: RerankerStatus
    reason: str | None = None


# A cross-encoder exposes `predict(list[(query, passage)]) -> list[float]`. We type it loosely so a
# test double or a real `sentence_transformers.CrossEncoder` both satisfy it.
CrossEncoderFactory = Callable[[str], Any]
_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def _offline_env_ok() -> bool:
    """True when HF offline mode is in effect (so a load cannot reach the network)."""
    return os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("TRANSFORMERS_OFFLINE") == "1"


def _default_factory(model_path: str) -> Any:  # pragma: no cover - requires the optional extra
    # Enforce offline BEFORE importing: the import itself must not phone home.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise _WeightsUnavailable(
            "sentence-transformers not installed: uv sync --package mcp-knowledge "
            "--extra local-reranker"
        ) from exc
    return CrossEncoder(model_path, local_files_only=True)


class _WeightsUnavailable(Exception):
    """Internal: the reranker weights/library are not available -> transparent RRF-only fallback."""


class Reranker:
    """Cross-encoder reranker; lazy, offline, with a transparent RRF-only fallback."""

    def __init__(
        self,
        *,
        model_path: str = DEFAULT_RERANKER_MODEL,
        enabled: bool = True,
        model_factory: CrossEncoderFactory | None = None,
        require_offline: bool = True,
    ) -> None:
        self._model_path = model_path
        self._enabled = enabled
        self._factory = model_factory
        self._require_offline = require_offline
        self._model: Any | None = None
        self._load_failed: str | None = None
        self._lock = threading.Lock()

    @property
    def model_path(self) -> str:
        return self._model_path

    def _load(self) -> Any:
        """Lazy load, once per process. Raises :class:`_WeightsUnavailable` on any failure."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:  # pragma: no cover - race only
                return self._model
            if self._require_offline and not _offline_env_ok() and self._factory is None:
                # Refuse an online load path: better a transparent fallback than silent egress.
                raise _WeightsUnavailable(
                    "HF_HUB_OFFLINE=1 is not set; refusing a non-offline reranker load (NFR-011)"
                )
            factory = self._factory or _default_factory
            if self._factory is None:
                with _CACHE_LOCK:
                    if self._model_path not in _CACHE:
                        _CACHE[self._model_path] = factory(self._model_path)
                    model = _CACHE[self._model_path]
            else:
                model = factory(self._model_path)
            self._model = model
            return model

    def rerank(self, query: str, passages: Sequence[str]) -> RerankOutcome:
        """Re-score ``passages`` for ``query``; fall back to input (RRF) order transparently.

        The input order is assumed to already be a correct ranking (the RRF fusion), so a fallback
        simply returns ``[0, 1, 2, ...]`` with ``status="disabled"``.
        """
        rrf_order = list(range(len(passages)))
        if not self._enabled:
            return RerankOutcome(rrf_order, "disabled", "reranker disabled by configuration")
        if not passages:
            return RerankOutcome(rrf_order, "enabled")
        try:
            model = self._load()
        except Exception as exc:  # noqa: BLE001 - any load failure => transparent RRF-only fallback
            self._load_failed = str(exc)
            reason = (
                str(exc)
                if isinstance(exc, _WeightsUnavailable)
                else f"reranker weights unavailable: {exc}"
            )
            return RerankOutcome(rrf_order, "disabled", reason)
        try:
            scores = model.predict([(query, passage) for passage in passages])
        except Exception as exc:  # noqa: BLE001 - any scoring failure => transparent fallback
            return RerankOutcome(rrf_order, "disabled", f"reranker scoring failed: {exc}")
        # Highest score first; stable tie-break by original index keeps it deterministic.
        order = sorted(rrf_order, key=lambda i: (-float(scores[i]), i))
        return RerankOutcome(order, "enabled")
