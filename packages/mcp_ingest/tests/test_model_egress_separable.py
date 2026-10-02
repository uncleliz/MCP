"""T-117 / TC-129 (egress arm) — the model-download egress is scoped to huggingface.co and is
**separable** from the Atlassian ingest egress (ADR-0023 §6d, FR-027/AC-002, FR-024/AC-002).

Three things are proven here, all with the one `check_egress` gate (no real download):

* ``huggingface.co`` is reachable on the model path **only** when it is on the allow-list;
* a host **other** than ``huggingface.co`` on the model path is refused (default-deny);
* the two egresses are **separable** — the Atlassian ingest host can be allow-listed and reached
  while ``huggingface.co`` is *not* on the list (model download deferred), and vice-versa. Opening
  one never opens the other.
"""

from __future__ import annotations

import pytest
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied, check_egress
from mcp_ingest.embedding.config import EmbeddingSettings
from mcp_ingest.embedding.download import HF_MODEL_HOST, download_model


def _stub_factory(model_id: str, **_kwargs: object):
    """A no-network stand-in for sentence-transformers (CI model stub)."""

    class _StubModel:
        max_seq_length = 8192

        def get_sentence_embedding_dimension(self) -> int:
            return 1024

        def encode(self, texts, **_kw):
            return [[0.0] * 1024 for _ in texts]

    return _StubModel()


def test_TC129_model_path_allows_huggingface_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """huggingface.co on the model path is admitted only when it is allow-listed."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "huggingface.co")
    # the model-download gate admits huggingface.co
    assert check_egress(HF_MODEL_HOST, settings=CommonSettings()) == "huggingface.co"
    # with it allow-listed, the controlled download (stub) runs
    result = download_model(
        EmbeddingSettings(provider="local"),
        common=CommonSettings(),
        model_factory=_stub_factory,
    )
    assert result.model_id == "BAAI/bge-m3"
    assert result.dimensions == 1024


def test_TC129_model_path_refuses_non_huggingface_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host other than huggingface.co on the model path is refused (default-deny)."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "huggingface.co")
    with pytest.raises(EgressDenied):
        check_egress("models.evil.example", settings=CommonSettings())
    # and the download itself refuses when the model host is NOT allow-listed
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")  # Atlassian open, HF closed
    with pytest.raises(EgressDenied):
        download_model(
            EmbeddingSettings(provider="local"),
            common=CommonSettings(),
            model_factory=_stub_factory,
        )


def test_TC129_egresses_are_separable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Atlassian ingest egress and the HF model egress are independent (ADR-0023 §6d).

    Allow-listing Atlassian does not open huggingface.co (model download deferred), and
    allow-listing huggingface.co does not open Atlassian.
    """
    # (a) Atlassian open, HF closed: the ingest host is reachable; the model host is denied.
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    assert check_egress("tnexwm.atlassian.net", settings=CommonSettings()) == "tnexwm.atlassian.net"
    with pytest.raises(EgressDenied):
        check_egress(HF_MODEL_HOST, settings=CommonSettings())

    # (b) HF open, Atlassian closed: the model host is reachable; the ingest host is denied.
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "huggingface.co")
    assert check_egress(HF_MODEL_HOST, settings=CommonSettings()) == "huggingface.co"
    with pytest.raises(EgressDenied):
        check_egress("tnexwm.atlassian.net", settings=CommonSettings())
