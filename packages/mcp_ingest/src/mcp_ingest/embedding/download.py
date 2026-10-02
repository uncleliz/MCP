"""Controlled embedding-model download (T-117 / T-118, CHG-003 · ADR-0023 §6d).

Everything the running system does stays **offline** (`HF_HUB_OFFLINE=1`): serving embeds with a
model already on disk and opens no socket. The *one* exception is a deliberate, controlled
download step — fetching the real embedding model (ADR-0010, provisionally ``BAAI/bge-m3``, 1024d)
so NFR-003 becomes *measurable*. This module is that step, and only that step.

Two invariants it enforces, each at a single point (L-001):

* **Egress is separable and scoped to ``huggingface.co``.** The model path's only allowed host is
  ``huggingface.co`` (plus its CDN, configured by the operator). It goes through the one
  :func:`mcp_common.egress.check_egress` gate, so a host other than ``huggingface.co`` on the model
  path is *refused* (default-deny, FR-024/AC-002). Opening Atlassian does **not** open HuggingFace
  and vice-versa — the two egresses are independent (ADR-0023 §6d): ingest from a real source runs
  fine with the model download deferred.

* **``HF_HUB_OFFLINE`` flips online only for the download, then back.** :func:`download_model`
  sets ``HF_HUB_OFFLINE=0`` (and ``TRANSFORMERS_OFFLINE=0``) **only** for the duration of the
  download call and restores the previous values in a ``finally`` — so before and after the
  controlled step, and during all serving, the process is offline again. A test asserts the flip
  and the restore (TC-129).

The real ``bge-m3`` download (~2 GB) is a ``@live`` operator step behind
``MCP_INGEST_ALLOW_LIVE_EGRESS=true`` + ``MCP_LIVE_TESTS`` and is **never** part of ``make ci``;
CI drives this with a *model stub* (an injected ``model_factory`` that fabricates a tiny model
without any network). The default production/test provider stays
:class:`~mcp_ingest.embedding.fake.DeterministicFakeProvider` until the operator downloads a real
model (ADR-0010 stays provisional; no recall/τ is invented here — L-002).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from mcp_common.config import CommonSettings
from mcp_common.egress import check_egress

from mcp_ingest.embedding.config import EmbeddingSettings
from mcp_ingest.embedding.errors import EmbeddingError
from mcp_ingest.embedding.local import LocalSentenceTransformerProvider

__all__ = [
    "HF_MODEL_HOST",
    "ModelDownloadResult",
    "download_model",
    "online_for_download",
]

#: The *only* host the model-download path may reach (ADR-0023 §6a/§6d). The operator may add a
#: CDN host (``cdn-lfs.huggingface.co``) to ``MCP_EGRESS_ALLOWLIST`` as well; nothing else on the
#: model path is permitted — default-deny.
HF_MODEL_HOST = "huggingface.co"

#: Environment variables that force HuggingFace / transformers into offline mode. Flipped to ``"0"``
#: only inside :func:`online_for_download`, restored immediately after.
_OFFLINE_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


class ModelDownloadResult:
    """What a controlled download produced, for the operator's log (never a secret)."""

    def __init__(self, *, model_id: str, dimensions: int, offline_restored: bool) -> None:
        self.model_id = model_id
        self.dimensions = dimensions
        #: True once ``HF_HUB_OFFLINE`` has been put back the way it was found (serving is offline).
        self.offline_restored = offline_restored

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ModelDownloadResult(model_id={self.model_id!r}, dimensions={self.dimensions}, "
            f"offline_restored={self.offline_restored})"
        )


@contextmanager
def online_for_download() -> Iterator[None]:
    """Flip ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE`` online for the body, then restore.

    This is the *only* place the process is allowed to be non-offline, and only for the controlled
    download. Whatever the variables were before (set to ``"1"``, set to something else, or unset)
    is restored exactly in the ``finally`` — including deleting a var that was previously unset — so
    serving after the download is offline again (TC-129).
    """
    saved: dict[str, str | None] = {var: os.environ.get(var) for var in _OFFLINE_VARS}
    try:
        for var in _OFFLINE_VARS:
            os.environ[var] = "0"
        yield
    finally:
        for var, previous in saved.items():
            if previous is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = previous


def download_model(
    settings: EmbeddingSettings | None = None,
    *,
    common: CommonSettings | None = None,
    model_factory: Callable[..., Any] | None = None,
    host: str = HF_MODEL_HOST,
) -> ModelDownloadResult:
    """Run the one controlled model-download step; return info about the loaded, offline provider.

    Steps, in order:

    1. **Egress gate (L-001).** Pass ``host`` (default ``huggingface.co``) through the single
       :func:`check_egress` gate. If ``huggingface.co`` is not on ``MCP_EGRESS_ALLOWLIST`` the call
       raises :class:`~mcp_common.egress.EgressDenied` *before* any flip or socket — and a host
       other than ``huggingface.co`` is likewise refused (FR-024/AC-002, FR-027/AC-002).
    2. **Online only for the download.** Inside :func:`online_for_download`, load the real model via
       :class:`LocalSentenceTransformerProvider` (its lazy ``_load`` fetches the weights the first
       time). ``HF_HUB_OFFLINE`` is ``0`` only here.
    3. **Back to offline.** The context manager restores the offline vars; serving embeds with the
       now-cached model and opens no socket.

    In CI a ``model_factory`` stub is injected: it fabricates a tiny model object with no network,
    so the whole flip/restore + egress contract is exercised without a real 2 GB download. The real
    download is the ``@live`` operator path (``MCP_INGEST_ALLOW_LIVE_EGRESS=true``), never in
    ``make ci``. ADR-0010 stays provisional; no recall/τ is produced here (L-002).
    """
    settings = settings or EmbeddingSettings()
    if settings.provider != "local":
        raise EmbeddingError(
            "download_model is for provider=local (the on-disk sentence-transformers model); "
            f"MCP_INGEST_EMBEDDING_PROVIDER={settings.provider}"
        )

    # 1. One egress gate for the model path — huggingface.co only, default-deny otherwise.
    check_egress(host, settings=common or CommonSettings())

    provider = LocalSentenceTransformerProvider(settings, model_factory=model_factory)

    # 2. Online strictly for the download/load; 3. restored to offline on exit.
    with online_for_download():
        assert os.environ.get("HF_HUB_OFFLINE") == "0"  # noqa: S101 - the controlled-step contract
        dimensions = provider.dimensions
        # Force the lazy load now, while online, so serving later never needs the network.
        provider.embed_query("warm up the model so serving stays offline")

    offline_restored = os.environ.get("HF_HUB_OFFLINE") != "0"
    return ModelDownloadResult(
        model_id=provider.model_id, dimensions=dimensions, offline_restored=offline_restored
    )
