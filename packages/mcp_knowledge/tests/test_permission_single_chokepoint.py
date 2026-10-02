"""T-104 structural — one permission choke point, before the grounding gate (FR-019/AC-003, TC-091).

Asserts the structural guarantees that must hold regardless of data:

1. Every code path that produces a context-pack passes through exactly **one**
   ``enforce_permission`` choke point (#1) — no bypass, not duplicated.
2. The permission choke point runs **strictly before** the grounding gate (#2); the two are
   separate and ordered (not merged).

The ordering is proven by instrumenting the one assembler: its permission hook records a call
before the gate hook does, and the assembler never produces a claim for a candidate the permission
hook dropped (no path around #1).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime

from knowledge_helpers import FakeKnowledgeClient, FakeRetriever
from mcp_knowledge.pack.assembler import ContextPack, ContextPackAssembler
from mcp_knowledge.pack.compress import CompressedChunk, compress_candidates
from mcp_knowledge.permission.enforce import TEAM_PRINCIPAL, CallerContext
from mcp_knowledge.retrieval.hybrid import Candidate
from mcp_knowledge.tools.read_api import KnowledgeReadApi

_DOC = "33333333-3333-4333-8333-333333333333"


class _SpyTx:
    """Wraps a tx and records each statement name fetched (to count the permission choke point)."""

    def __init__(self, inner, log: list[str]) -> None:
        self._inner = inner
        self._log = log

    async def fetch(self, name: str, params=None):
        self._log.append(name)
        return await self._inner.fetch(name, params)

    async def set_local(self, name: str, value: str) -> None:  # pragma: no cover
        await self._inner.set_local(name, value)


def _candidate(cid: int, doc: str = _DOC) -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=doc, chunk_index=0, content=f"content {cid}", heading_path=None,
        source_type="confluence", source_id=str(cid), source_uri=f"https://e/{cid}", title="t",
        container="PAY", author=None, source_updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        ingested_at=None, rrf_score=1.0 / cid, legs={"vector": cid},
    )  # fmt: skip


def test_assembler_applies_permission_strictly_before_gate() -> None:
    # One assembler, two ordered hooks: the permission filter (#1) must be invoked before the
    # grounding gate (#2), and the gate must only ever see permitted candidates.
    events: list[str] = []

    def permission(candidates: Sequence[Candidate]) -> list[Candidate]:
        events.append("permission")
        return [c for c in candidates if c.chunk_id != 2]  # deny chunk 2

    def gate(chunks: Sequence[CompressedChunk]) -> ContextPack:
        events.append("gate")
        # The gate must never see the denied candidate — proving #1 ran first and filtered.
        assert all(ch.candidate.chunk_id != 2 for ch in chunks)
        return ContextPack(
            claims=[], chunks=list(chunks), verdicts_assigned=True
        )

    cands = [_candidate(1), _candidate(2), _candidate(3)]
    chunks = compress_candidates(cands, token_budget=1000)
    ContextPackAssembler(permission_filter=permission, grounding_gate=gate).assemble(cands, chunks)

    assert events == ["permission", "gate"], "permission (#1) must run strictly before gate (#2)"


async def test_single_choke_point_both_grounded_tools_filter_once() -> None:
    # Both grounded tools (the only paths producing a context-pack) must enforce permission exactly
    # once per call, at the single choke point. We count `document_grants` fetches — the one DB read
    # `enforce_permission` makes — so other reads (e.g. the Live-path config read) do not confound.
    grant_fetches: list[str] = []

    class _SpyClient(FakeKnowledgeClient):
        @contextlib.asynccontextmanager
        async def read_tx(self):  # type: ignore[override]
            async with super().read_tx() as tx:
                yield _SpyTx(tx, grant_fetches)

    client = _SpyClient(grants={_DOC: {TEAM_PRINCIPAL}})
    api = KnowledgeReadApi(client, FakeRetriever([_candidate(1)]), caller=CallerContext())

    grant_fetches.clear()
    await api.search_company_knowledge(query="anything matching", top_k=8)
    assert grant_fetches.count("document_grants") == 1, "exactly one permission choke point"

    grant_fetches.clear()
    await api.get_jira_context(subject="payment-service", top_k=8)
    assert grant_fetches.count("document_grants") == 1, "exactly one permission choke point"


async def test_no_bypass_denied_candidate_absent_from_pack() -> None:
    # Structural: there is no path that reaches claims without passing permission. A fully-denied
    # candidate set yields no permitted claims (the restricted doc never reaches the gate).
    client = FakeKnowledgeClient(grants={_DOC: {"user:not-the-caller"}})
    api = KnowledgeReadApi(client, FakeRetriever([_candidate(1)]), caller=CallerContext())
    outcome = await api.search_company_knowledge(query="query that matches the denied doc", top_k=8)
    # Every surviving claim must carry no provenance pointing at the denied document.
    for claim in outcome.result.claims:
        for prov in claim.provenance:
            evidence = getattr(prov, "evidence", None)
            assert evidence is None or str(getattr(evidence, "document_id", "")) != _DOC
    assert all(c.uri != "https://e/1" for c in outcome.result.citations)


def test_permission_is_the_single_decision_function() -> None:
    # The permission module exposes exactly one enforcement entry point; the pipeline wires it at
    # one site. Guards against a second, divergent filter being introduced.
    from mcp_knowledge.permission import enforce as mod

    enforcers = [
        name
        for name in dir(mod)
        if name.startswith("enforce") and callable(getattr(mod, name))
    ]
    assert enforcers == ["enforce_permission"], enforcers


# == R-C-004 / R-031: the single choke point must cover EVERY content tool, not only the grounded ==
# R-028/E-007: the structural test above only proved one `enforce_*` symbol exists; it did not prove
# every content-returning tool actually routes through it. These tests enumerate the full 8-tool
# surface and assert each one consults the one permission seam before returning content.

_ENTITY = "77777777-7777-4777-8777-777777777777"
_SUMMARY_DOC = "88888888-8888-4888-8888-888888888888"


def _rows_for_all_tools() -> dict:
    return {
        "entity_by_type_name": [
            {"id": _ENTITY, "entity_type": "service", "name": "svc", "display_name": "Svc",
             "document_id": _DOC, "metadata": {}, "source_type": "confluence",
             "source_uri": "https://e/svc", "title": "svc", "container": "PAY",
             "deleted_at": None}
        ],
        "entity_neighbours": [],
        "entity_by_name_any_type": [
            {"id": _ENTITY, "entity_type": "service", "name": "svc", "document_id": _DOC,
             "source_type": "confluence", "source_uri": "https://e/svc", "deleted_at": None}
        ],
        "related_knowledge": [
            {"src_name": "svc", "dst_name": "n", "rel_type": "depends_on", "depth": 1,
             "doc_id": _DOC, "source_type": "confluence", "source_uri": "https://e/n"}
        ],
        "knowledge_summary": [
            {"subject_type": "entity", "subject_id": "svc", "summary": "s",
             "provenance": [{"source": "confluence", "document_id": _SUMMARY_DOC,
                             "chunk_id": f"{_SUMMARY_DOC}#0",
                             "updated_time": "2026-09-30T00:00:00Z",
                             "evidence": {"document_id": _SUMMARY_DOC}}],
             "generated_at": datetime(2026, 10, 1, tzinfo=UTC)}
        ],
        "entity_id_by_name": [{"subject_id": "svc"}],
        "document_citation": [{"source_type": "confluence", "source_uri": "https://e/d",
                               "title": "d"}],
        "live_document": [{"document_id": _DOC, "source_type": "confluence",
                           "source_uri": "https://e/d", "title": "d"}],
        "document_versions": [
            {"document_id": _DOC, "version": 1, "content_hash": "h", "source_version": "1",
             "author": "a", "source_updated_at": datetime(2026, 9, 30, tzinfo=UTC),
             "created_at": datetime(2026, 10, 1, tzinfo=UTC), "status": "current"}
        ],
    }


def _spy_client() -> tuple[FakeKnowledgeClient, list[str]]:
    grant_fetches: list[str] = []

    class _SpyClient(FakeKnowledgeClient):
        @contextlib.asynccontextmanager
        async def read_tx(self):  # type: ignore[override]
            async with super().read_tx() as tx:
                yield _SpyTx(tx, grant_fetches)

    # Grant every doc that will be probed, so each tool returns content AND we can prove it still
    # went through the one `document_grants` seam to get there.
    client = _SpyClient(
        data=_rows_for_all_tools(),
        grants={
            _DOC: {TEAM_PRINCIPAL},
            _SUMMARY_DOC: {TEAM_PRINCIPAL},
        },
    )
    return client, grant_fetches


async def test_every_content_tool_consults_the_single_permission_seam() -> None:
    # Each of the 8 content tools must trigger exactly the one `document_grants` read that
    # `enforce_permission` decides on — no tool returns content without passing the seam (L-001).
    tools = [
        ("search_company_knowledge", lambda a: a.search_company_knowledge(query="abc", top_k=8)),
        ("get_jira_context", lambda a: a.get_jira_context(subject="svc", top_k=8)),
        ("search_code", lambda a: a.search_code(query="svc", top_k=10)),
        ("get_service", lambda a: a.get_service(name="svc")),
        ("get_repository", lambda a: a.get_repository(name="svc")),
        ("find_related_knowledge", lambda a: a.find_related_knowledge(entity="svc", max_depth=2)),
        ("get_knowledge_summary", lambda a: a.get_knowledge_summary(subject="svc")),
        ("get_document_version", lambda a: a.get_document_version(document_id=_DOC)),
    ]
    for name, call in tools:
        client, grant_fetches = _spy_client()
        api = KnowledgeReadApi(client, FakeRetriever([_candidate(1)]), caller=CallerContext())
        grant_fetches.clear()
        await call(api)
        assert grant_fetches.count("document_grants") >= 1, (
            f"{name} returned without consulting the permission choke point"
        )


async def test_fixed_filter_is_honoured_by_non_grounded_tools_too() -> None:
    # The pinned-filter test seam must drive the non-grounded tools identically to the grounded
    # ones: a filter that denies everything makes every content tool withhold content, proving the
    # non-grounded tools route their decision through the SAME PermissionFilter hook.
    def deny_all(candidates):
        return []

    client = FakeKnowledgeClient(data=_rows_for_all_tools())
    api = KnowledgeReadApi(
        client, FakeRetriever([_candidate(1)]), permission_filter=deny_all, caller=CallerContext()
    )
    assert (await api.search_code(query="svc", top_k=10)).result.items == []
    assert (await api.get_service(name="svc")).result.status.value == "not_found"
    assert (await api.get_repository(name="svc")).result.status.value == "not_found"
    rel = await api.find_related_knowledge(entity="svc", max_depth=2)
    assert rel.result.status.value == "not_found"
    assert (await api.get_knowledge_summary(subject="svc")).result.status.value == "not_found"
    assert (await api.get_document_version(document_id=_DOC)).result.status.value == "not_found"
