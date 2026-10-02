"""Shared fakes for the mcp-knowledge tool tests (no database, no network).

``FakeKnowledgeClient`` answers the named domain statements from a dict keyed by statement name;
``FakeRetriever`` returns a fixed candidate list so the hybrid pipeline (rerank → compress →
assembler) runs end to end without Postgres.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from mcp_knowledge.retrieval.hybrid import Candidate


class _FakeTx:
    def __init__(
        self,
        data: dict[str, list[dict[str, Any]]],
        grants: dict[str, set[str]] | None,
    ) -> None:
        self._data = data
        self._grants = grants

    async def fetch(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if name == "document_grants" and self._grants is not None:
            return self._document_grants(params or {})
        return list(self._data.get(name, []))

    def _document_grants(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Mirror the SQL ``document_grants`` default-deny join over the in-memory grant map.

        Returns a row only when a candidate document has a grant for one of the caller's
        principals — exactly the intersection the real statement computes, so the fake exercises
        the same default-deny semantics without a database.
        """
        wanted_docs = set(params.get("document_ids") or [])
        principals = set(params.get("principals") or [])
        rows: list[dict[str, Any]] = []
        for document_id in wanted_docs:
            for principal in self._grants.get(document_id, set()) & principals:
                rows.append({"document_id": document_id, "principal": principal})
        return rows

    async def set_local(self, name: str, value: str) -> None:  # pragma: no cover - unused in fakes
        return None


class FakeKnowledgeClient:
    """Serves canned rows per named statement; `read_tx()` yields a `_FakeTx`.

    ``grants`` maps ``document_id -> set(principal)`` for the permission choke point. When omitted
    it defaults to granting the pseudo-principal ``'*team*'`` on **every** candidate document the
    ``document_grants`` lookup asks about, so tests that only care about the grounding path keep
    their candidates (the TEAM-ONLY corpus baseline, ADR-0016 A1). The adversarial permission test
    passes an explicit map to withhold a grant.
    """

    def __init__(
        self,
        data: dict[str, list[dict[str, Any]]] | None = None,
        *,
        grants: dict[str, set[str]] | None = None,
        grant_team_by_default: bool = True,
    ) -> None:
        self._data = data or {}
        self._grants = grants
        self._grant_team_by_default = grant_team_by_default and grants is None

    @contextlib.asynccontextmanager
    async def read_tx(self) -> AsyncIterator[_FakeTx]:
        yield _FakeTx(self._data, self._effective_grants())

    def _effective_grants(self) -> dict[str, set[str]] | None:
        if self._grants is not None:
            return self._grants
        if self._grant_team_by_default:
            return _GrantAllTeam()
        return None


class _GrantAllTeam(dict):
    """A grant map that grants ``'*team*'`` on any document asked for (default team-visible)."""

    def get(self, key: Any, default: Any = None) -> set[str]:  # type: ignore[override]
        return {"*team*"}


class FakeRetriever:
    """A `HybridRetriever` stand-in returning fixed candidates (ignores the query)."""

    def __init__(
        self, candidates: list[Candidate] | None = None, *, model_id: str = "fake/m"
    ) -> None:
        self._candidates = candidates or []

        class _P:
            model_id = "fake/hashed-bow"

        self._provider = _P()

    async def retrieve(self, query: Any) -> list[Candidate]:
        return list(self._candidates)


def make_candidate(
    *,
    chunk_id: int = 1,
    document_id: str = "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    content: str = "payment worker retry backoff three times",
    source_type: str = "gitlab",
    source_uri: str = "https://gitlab.example.com/x",
    title: str | None = "worker",
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content=content,
        heading_path=None,
        source_type=source_type,
        source_id="g1",
        source_uri=source_uri,
        title=title,
        container="payments/worker",
        author="an.nguyen",
        source_updated_at=datetime(2026, 9, 21, 4, 10, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 1, 3, 7, tzinfo=UTC),
        rrf_score=0.0163,
        legs={"vector": 1, "keyword": 1},
    )


# Canned domain rows -----------------------------------------------------------------------------

ENTITY_ROW = {
    "id": "11111111-1111-4111-8111-111111111111",
    "entity_type": "service",
    "name": "payment-service",
    "display_name": "Payment Service",
    "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "metadata": {"language": "java", "team": "payments"},
    "source_type": "confluence",
    "source_uri": "https://wiki.example.com/x",
    "title": "payment-service",
    "container": "PAY",
    "deleted_at": None,
}

NEIGHBOUR_ROWS = [
    {"rel_type": "depends_on", "entity_type": "service", "name": "ledger-service"},
    {"rel_type": "documented_by", "entity_type": "document", "name": "Payment retry policy"},
]

SUMMARY_ROW = {
    "subject_type": "entity",
    "subject_id": "payment-service",
    "summary": "payment-service orchestrates payments; owner payments-team.",
    "provenance": [
        {
            "source": "confluence",
            "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "chunk_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301#0",
            "updated_time": "2026-09-30T03:15:00Z",
            "evidence": {
                "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
                "chunk_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301#0",
            },
        }
    ],
    "generated_at": datetime(2026, 10, 1, 3, 10, tzinfo=UTC),
}

DOCUMENT_ROW = {
    "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "source_type": "confluence",
    "source_uri": "https://wiki.example.com/x",
    "title": "Payment retry policy",
}

VERSION_ROWS = [
    {
        "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "version": 12,
        "content_hash": "sha256:ab",
        "source_version": "12",
        "author": "an.nguyen",
        "source_updated_at": datetime(2026, 9, 30, 3, 15, tzinfo=UTC),
        "created_at": datetime(2026, 10, 1, 3, 7, tzinfo=UTC),
        "status": "current",
    },
    {
        "document_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        "version": 11,
        "content_hash": "sha256:cd",
        "source_version": "11",
        "author": "an.nguyen",
        "source_updated_at": datetime(2026, 9, 20, tzinfo=UTC),
        "created_at": datetime(2026, 9, 21, 3, 7, tzinfo=UTC),
        "status": "superseded",
    },
]
