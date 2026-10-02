"""Targeted unit tests for the knowledge render/client/read-api branches (coverage of new code)."""

from __future__ import annotations

import pytest
from knowledge_helpers import (
    DOCUMENT_ROW,
    SUMMARY_ROW,
    VERSION_ROWS,
    FakeKnowledgeClient,
    FakeRetriever,
)
from mcp_common.envelope import (
    Citation,
    Claim,
    ClaimEvidence,
    ClaimPosition,
    ClaimProvenance,
    DataFreshness,
    GroundedResult,
    GroundedStatus,
    GroundingSummary,
    GroundingVerdict,
    Meta,
    SourceType,
)
from mcp_knowledge.client import KnowledgeClient
from mcp_knowledge.tools.grounded import render_grounded_text
from mcp_knowledge.tools.read_api import KnowledgeReadApi

pytestmark = pytest.mark.asyncio


def _meta(**over) -> Meta:
    from datetime import UTC, datetime

    base = dict(
        source=SourceType.PGVECTOR, returned=1, has_more=False, next_cursor=None,
        truncated=False, elapsed_ms=1, as_of=datetime.now(UTC), query_echo={},
    )
    base.update(over)
    return Meta(**base)


def _prov() -> ClaimProvenance:
    from datetime import UTC, datetime

    return ClaimProvenance(
        source=SourceType.CONFLUENCE,
        updated_time=datetime(2026, 9, 30, tzinfo=UTC),
        evidence=ClaimEvidence(document_id="d", chunk_id="d#0", url="https://wiki/x"),
    )


def test_render_fact_and_conflict_branches() -> None:
    fact = Claim(text="timeout là 3s", grounding=GroundingVerdict.FACT, provenance=[_prov()])
    conflict = Claim(
        text="read timeout", grounding=GroundingVerdict.CONFLICT,
        positions=[ClaimPosition(value="5s", provenance=[_prov()]),
                   ClaimPosition(value="10s", provenance=[_prov()])],
    )
    result = GroundedResult(
        status=GroundedStatus.PARTIAL,
        claims=[fact, conflict],
        grounding_summary=GroundingSummary(fact=1, low_confidence=0, unknown=0, conflict=1),
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://wiki/x")],
        meta=_meta(),
    )
    text = render_grounded_text(result, query_description="timeout")
    assert "timeout là 3s" in text
    assert "CONFLICT" in text and "5s" in text and "10s" in text
    assert "Nguồn: x — https://wiki/x" in text


def test_render_reranker_disabled_note() -> None:
    result = GroundedResult(
        status=GroundedStatus.INSUFFICIENT_EVIDENCE,
        claims=[],
        grounding_summary=GroundingSummary(
            fact=0, low_confidence=0, unknown=0, conflict=0, reranker="disabled"
        ),
        citations=[],
        meta=_meta(returned=0, warnings=["heads up"]),
    )
    text = render_grounded_text(result)
    assert "RRF-only" in text and "heads up" in text


async def test_verify_credentials_reports_db_down_without_raising() -> None:
    # unreachable DSN: verify_credentials returns a not-ok report, never raises (DSN stays secret)
    client = KnowledgeClient("postgresql://mcp_query_ro:pw@127.0.0.1:1/none")

    class _P:
        model_id = "fake/hashed-bow"
        dimensions = 1024

    report = await client.verify_credentials(_P())  # type: ignore[arg-type]
    assert not report.ok
    assert "pw" not in " ".join(report.reasons)
    await client.aclose()


async def test_credential_check_hook_returns_bool() -> None:
    client = KnowledgeClient("postgresql://mcp_query_ro@127.0.0.1:1/none")

    class _P:
        model_id = "fake/hashed-bow"
        dimensions = 1024

    assert await client.credential_check(_P()) is False  # type: ignore[arg-type]
    await client.aclose()


async def test_get_knowledge_summary_resolves_entity_name_to_id() -> None:
    client = FakeKnowledgeClient(
        {
            "entity_id_by_name": [{"subject_id": "11111111-1111-4111-8111-111111111111"}],
            "knowledge_summary": [SUMMARY_ROW],
            "document_citation": [DOCUMENT_ROW],
        }
    )
    api = KnowledgeReadApi(client, FakeRetriever([]))
    out = await api.get_knowledge_summary(subject="payment-service", subject_type="entity")
    assert out.result.status.value == "ok"
    assert out.result.items[0]["provenance"][0]["evidence"]["document_id"]


async def test_get_document_version_specific_version() -> None:
    uid = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    client = FakeKnowledgeClient(
        {"live_document": [DOCUMENT_ROW], "document_versions": [VERSION_ROWS[0]]}
    )
    api = KnowledgeReadApi(client, FakeRetriever([]))
    out = await api.get_document_version(document_id=uid, version=12)
    assert out.result.status.value == "ok"
    assert out.result.items[0]["version"] == 12


async def test_get_document_version_rejects_bad_uuid_and_bad_version() -> None:
    from mcp_common.errors import ToolError

    api = KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([]))
    with pytest.raises(ToolError):
        await api.get_document_version(document_id="not-a-uuid")
    with pytest.raises(ToolError):
        await api.get_document_version(
            document_id="3f2504e0-4f89-11d3-9a0c-0305e82c3301", version=0
        )


async def test_search_code_and_company_knowledge_reject_short_queries() -> None:
    from mcp_common.errors import ToolError

    api = KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([]))
    with pytest.raises(ToolError):
        await api.search_code(query="a")
    with pytest.raises(ToolError):
        await api.search_company_knowledge(query="ab")
    with pytest.raises(ToolError):
        await api.get_jira_context(subject="")


async def test_get_service_rejects_overlong_name() -> None:
    from mcp_common.errors import ToolError

    api = KnowledgeReadApi(FakeKnowledgeClient(), FakeRetriever([]))
    with pytest.raises(ToolError):
        await api.get_service(name="x" * 300)


async def test_data_freshness_passthrough_in_grounded_builder() -> None:
    import time

    from mcp_knowledge.pack.assembler import ContextPack
    from mcp_knowledge.tools.grounded import build_grounded_result

    fresh = DataFreshness(staleness_hours=2.9, embedding_model="fake/hashed-bow")
    result = build_grounded_result(
        ContextPack(claims=[], chunks=[]),
        started=time.monotonic(),
        query_echo={"query": "x"},
        reranker_status="enabled",
        data_freshness=fresh,
    )
    assert result.meta.data_freshness is not None
    assert result.meta.data_freshness.embedding_model == "fake/hashed-bow"
