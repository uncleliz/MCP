"""T-097 / TC-081 — no outbound socket is opened during rerank or compression (ADR-0020, NFR-011).

Any attempt to open an outbound network connection (``socket.connect`` /
``socket.create_connection``) during the rerank or compression path makes the test fail. This is
the "zero outbound sockets" assertion of TC-081 for the compute stages the knowledge engine runs
in-process; the DB connection is a separate concern tested in `test_integration.py` (and uses a
local socket to Postgres only, not egress).
"""

from __future__ import annotations

import socket

import pytest
from mcp_knowledge.pack.compress import compress_candidates
from mcp_knowledge.rerank.local import Reranker
from mcp_knowledge.retrieval.hybrid import Candidate


class _EgressDetected(AssertionError):
    pass


@pytest.fixture
def no_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_connect(self: socket.socket, address: object) -> None:  # noqa: ANN001
        raise _EgressDetected(f"outbound socket connect attempted to {address!r}")

    def blocked_create_connection(address: object, *args: object, **kwargs: object) -> None:
        raise _EgressDetected(f"outbound socket.create_connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect, raising=True)
    monkeypatch.setattr(socket, "create_connection", blocked_create_connection, raising=True)


class FakeCrossEncoder:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [float(len(p)) for _q, p in pairs]


def _candidate(cid: int) -> Candidate:
    return Candidate(
        chunk_id=cid, document_id="d", chunk_index=0, content="payment retry backoff dlq " * 10,
        heading_path="h", source_type="confluence", source_id=str(cid),
        source_uri=f"https://e/{cid}", title="t", container="PAY", author="a",
        source_updated_at=None, ingested_at=None, rrf_score=0.1, legs={"vector": 1},
    )  # fmt: skip


def test_rerank_opens_no_outbound_socket(no_egress: None) -> None:
    r = Reranker(enabled=True, model_factory=lambda p: FakeCrossEncoder(p), require_offline=False)
    outcome = r.rerank("payment retry", ["short", "a longer passage", "mid"])
    assert outcome.status == "enabled"  # ran, and no egress was attempted (fixture would fail)


def test_rerank_fallback_opens_no_outbound_socket(no_egress: None) -> None:
    # Even the fallback path (weights absent) must not try to reach the network.
    def failing(path: str) -> object:
        raise FileNotFoundError("no weights")

    r = Reranker(enabled=True, model_factory=failing, require_offline=False)
    outcome = r.rerank("q", ["a", "b"])
    assert outcome.status == "disabled"


def test_compression_opens_no_outbound_socket(no_egress: None) -> None:
    chunks = compress_candidates([_candidate(i) for i in range(5)], token_budget=200)
    assert chunks  # compression ran entirely in-process, no egress
