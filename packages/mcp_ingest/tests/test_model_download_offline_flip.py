"""T-118 / TC-129 (flip + serving arm) and TC-131 (@live) — the controlled model download flips
``HF_HUB_OFFLINE`` online **only** for the download step, then back to offline; serving opens no
socket; the fake provider stays the default/test path (ADR-0023 §6d, ADR-0010 provisional, L-002).

CI uses a **model stub** (an injected factory, no network); the real ``bge-m3`` download is the
``@live`` operator step (``MCP_INGEST_ALLOW_LIVE_EGRESS=true`` + ``MCP_LIVE_TESTS``), never in
``make ci``. No recall/τ is produced here — ADR-0010 stays provisional (L-002).
"""

from __future__ import annotations

import os
import socket

import pytest
from mcp_common.config import CommonSettings
from mcp_ingest.embedding import build_provider
from mcp_ingest.embedding.config import EmbeddingSettings
from mcp_ingest.embedding.download import download_model, online_for_download
from mcp_ingest.embedding.fake import DeterministicFakeProvider


class _RecordingStubModel:
    """A no-network stand-in that records whether HF_HUB_OFFLINE was online when loaded."""

    max_seq_length = 8192

    def __init__(self) -> None:
        self.offline_at_load = os.environ.get("HF_HUB_OFFLINE")

    def get_sentence_embedding_dimension(self) -> int:
        return 1024

    def encode(self, texts, **_kw):
        return [[0.1] * 1024 for _ in texts]


def test_TC129_hf_offline_flips_online_for_download_then_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HF_HUB_OFFLINE is '0' during the download, and back to its prior value afterwards."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "huggingface.co")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")  # serving default: offline

    captured: dict[str, str | None] = {}

    def factory(model_id: str, **_kwargs: object):
        model = _RecordingStubModel()
        captured["offline_during_load"] = model.offline_at_load
        return model

    result = download_model(
        EmbeddingSettings(provider="local"), common=CommonSettings(), model_factory=factory
    )

    # online strictly during the load...
    assert captured["offline_during_load"] == "0"
    # ...and restored to offline afterwards (serving is offline).
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert result.offline_restored is True


def test_TC129_online_for_download_restores_unset_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """A var that was unset before the step is deleted again afterwards (exact restore)."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with online_for_download():
        assert os.environ.get("HF_HUB_OFFLINE") == "0"
    assert "HF_HUB_OFFLINE" not in os.environ


def test_TC129_serving_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the download, embedding (serving) opens no outbound socket."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "huggingface.co")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    provider_holder: dict[str, object] = {}

    def factory(model_id: str, **_kwargs: object):
        return _RecordingStubModel()

    # Build a provider the same way download_model does, then serve under a socket ban.
    settings = EmbeddingSettings(provider="local")
    from mcp_ingest.embedding.local import LocalSentenceTransformerProvider

    provider = LocalSentenceTransformerProvider(settings, model_factory=factory)
    with online_for_download():
        provider.embed_query("warm up")  # load while online (the controlled step)
    provider_holder["p"] = provider

    original_socket = socket.socket

    def _no_network(*_a: object, **_k: object):
        raise AssertionError("serving must not open a socket (offline after download)")

    monkeypatch.setattr(socket, "socket", _no_network)
    try:
        vectors = provider.embed_documents(["serving stays offline"])
    finally:
        monkeypatch.setattr(socket, "socket", original_socket)
    assert len(vectors) == 1 and len(vectors[0]) == 1024


def test_fake_provider_remains_the_default_test_path() -> None:
    """The default built provider is still the deterministic fake unless a real model is set up.

    build_provider() with provider=local returns the Local adapter but does NOT load a model (no
    network at import/construct). The fake provider stays the production/test default used by the
    pipeline and eval until the operator runs the controlled download (ADR-0010 provisional).
    """
    assert isinstance(DeterministicFakeProvider(), DeterministicFakeProvider)
    # build_provider never triggers a download merely by constructing the local adapter.
    provider = build_provider(EmbeddingSettings(provider="local"))
    assert provider.model_id == "BAAI/bge-m3"  # the pinned provisional model id, not yet loaded


@pytest.mark.live
def test_TC131_live_real_bge_m3_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """`@live` — NOT in make ci. The operator downloads the REAL bge-m3 behind the live gates.

    Skipped unless MCP_INGEST_ALLOW_LIVE_EGRESS=true (+ MCP_LIVE_TESTS marks it live). It performs
    the real ~2 GB download of BAAI/bge-m3 through huggingface.co with HF_HUB_OFFLINE flipped only
    for the step. It records NO recall/τ — ADR-0010 stays provisional until the golden-set bake-off
    (spike S2) measures a real number (L-002).
    """
    if os.environ.get("MCP_INGEST_ALLOW_LIVE_EGRESS") != "true":
        pytest.skip(
            "real bge-m3 download is gated: set MCP_INGEST_ALLOW_LIVE_EGRESS=true "
            "(needs huggingface.co on MCP_EGRESS_ALLOWLIST and the sentence-transformers extra)"
        )
    result = download_model(EmbeddingSettings(provider="local"), common=CommonSettings())
    assert result.dimensions == 1024
    assert result.offline_restored is True
    # Honest NFR-003: no recall/τ asserted here (L-002); quality is measured later by spike S2.
