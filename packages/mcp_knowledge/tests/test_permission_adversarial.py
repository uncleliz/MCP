"""T-104 adversarial — restricted document never leaks (FR-019/AC-002, TC-090, L-001).

The corpus contains a ``restricted`` document whose content matches the query, but the caller holds
no grant on it (default-deny). The test issues the matching query and asserts the restricted
document does **not** appear as a candidate after enforcement, is **not** in the context-pack
claims, and is **not** cited — it is excluded *before* context assembly at choke point #1. The test
fails if the document leaks into any of the three.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.envelope import GroundedResult
from mcp_knowledge.permission.enforce import TEAM_PRINCIPAL, CallerContext
from mcp_knowledge.retrieval.hybrid import Candidate
from mcp_knowledge.tools.read_api import KnowledgeReadApi

CONTRACT = load_contract()
pytestmark = pytest.mark.asyncio

_PERMITTED_DOC = "11111111-1111-4111-8111-111111111111"
_RESTRICTED_DOC = "22222222-2222-4222-8222-222222222222"
_RESTRICTED_URI = "https://wiki.example.com/restricted-salaries"


def _candidate(doc: str, cid: int, uri: str, content: str) -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=doc, chunk_index=0, content=content, heading_path=None,
        source_type="confluence", source_id=str(cid), source_uri=uri, title="t", container="PAY",
        author=None, source_updated_at=datetime(2026, 9, 1, tzinfo=UTC), ingested_at=None,
        rrf_score=1.0 / cid, legs={"vector": cid, "keyword": cid},
    )  # fmt: skip


def _resolve(node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        target: Any = CONTRACT
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _validate_grounded(operation_id: str, payload: dict) -> None:
    from jsonschema import Draft202012Validator

    response = _resolve(operations_by_id(CONTRACT)[operation_id]["responses"]["200"])
    schema = response["content"]["application/json"]["schema"]
    root = {"components": CONTRACT["components"], **schema}
    errors = sorted(
        Draft202012Validator(root).iter_errors(payload), key=lambda e: list(e.absolute_path)
    )
    assert not errors, "\n".join(f"- {e.message}" for e in errors[:5])
    GroundedResult.model_validate(payload)


def _api_with_restricted_match() -> KnowledgeReadApi:
    # The restricted doc is the STRONGEST match (best rrf_score) — the adversary crafted the query
    # to retrieve it. Only `*team*` is granted on the permitted doc; the restricted doc has an
    # explicit grant for a different principal the caller does NOT hold.
    candidates = [
        _candidate(_RESTRICTED_DOC, 2, _RESTRICTED_URI, "executive salary bands 2026 confidential"),
        _candidate(_PERMITTED_DOC, 1, "https://wiki.example.com/ok", "salary review process"),
    ]
    grants = {
        _PERMITTED_DOC: {TEAM_PRINCIPAL},
        _RESTRICTED_DOC: {"user:cfo"},  # caller is a plain team member, not user:cfo
    }
    client = FakeKnowledgeClient(grants=grants)
    return KnowledgeReadApi(client, FakeRetriever(candidates), caller=CallerContext())


async def test_restricted_doc_not_a_candidate_after_enforcement() -> None:
    from mcp_knowledge.permission.enforce import enforce_permission, load_grants

    client = FakeKnowledgeClient(
        grants={_PERMITTED_DOC: {TEAM_PRINCIPAL}, _RESTRICTED_DOC: {"user:cfo"}}
    )
    cands = [
        _candidate(_RESTRICTED_DOC, 2, _RESTRICTED_URI, "executive salary bands 2026 confidential"),
        _candidate(_PERMITTED_DOC, 1, "https://wiki.example.com/ok", "salary review process"),
    ]
    grants = await load_grants(client, cands, CallerContext())
    kept = enforce_permission(cands, grants)
    kept_docs = {c.document_id for c in kept}
    assert _RESTRICTED_DOC not in kept_docs  # dropped before anything downstream sees it
    assert _PERMITTED_DOC in kept_docs


async def test_restricted_doc_not_in_pack_or_citation() -> None:
    outcome = await _api_with_restricted_match().search_company_knowledge(
        query="executive salary bands confidential", top_k=8
    )
    payload = outcome.result.model_dump(mode="json")
    _validate_grounded("search_company_knowledge", payload)

    # 1) not in any claim text/provenance
    for claim in outcome.result.claims:
        for prov in claim.provenance:
            evidence = prov.evidence
            assert evidence is None or str(getattr(evidence, "document_id", "")) != _RESTRICTED_DOC
    serialized = str(payload)
    assert _RESTRICTED_DOC not in serialized, "restricted document_id leaked into the pack"
    assert _RESTRICTED_URI not in serialized, "restricted source_uri leaked into the pack"

    # 2) not cited
    for citation in outcome.result.citations:
        assert citation.uri != _RESTRICTED_URI
        assert str((citation.locator or {}).get("document_id", "")) != _RESTRICTED_DOC

    # 3) not in the rendered text Claude reads
    from mcp_knowledge.tools.grounded import render_grounded_text

    text = render_grounded_text(outcome.result, query_description=outcome.query_description)
    assert _RESTRICTED_URI not in text


async def test_fully_restricted_corpus_yields_insufficient_evidence() -> None:
    # Every matching candidate is restricted-no-grant => nothing survives => honest UNKNOWN,
    # never a fabricated answer from the restricted content.
    candidates = [
        _candidate(_RESTRICTED_DOC, 2, _RESTRICTED_URI, "executive salary bands 2026 confidential"),
    ]
    client = FakeKnowledgeClient(grants={_RESTRICTED_DOC: {"user:cfo"}})
    api = KnowledgeReadApi(client, FakeRetriever(candidates), caller=CallerContext())
    outcome = await api.search_company_knowledge(query="executive salary bands", top_k=8)
    payload = outcome.result.model_dump(mode="json")
    _validate_grounded("search_company_knowledge", payload)
    assert _RESTRICTED_URI not in str(payload)
    assert outcome.result.citations == []


# == R-C-001 / R-C-004 (E-mcp-data-platform-007): zero leak through EACH of the 8 content tools ===
# The HIGH finding R-028 was that the permission choke point was wired on only 2 of 8 content tools.
# These tests drive the OTHER six (and re-cover the two grounded) with a restricted document the
# caller holds no grant on, and assert the restricted document_id / source_uri never appears in the
# tool output. Each tool must deny via the SAME `enforce_permission` decision (single choke point),
# not by a per-tool special case.

_ENTITY_ID = "44444444-4444-4444-8444-444444444444"
_NEIGHBOUR_ID = "55555555-5555-4555-8555-555555555555"


def _restricted_rows() -> dict:
    """Canned domain rows that all reference the restricted document, so a tool that forgets the
    permission gate would surface ``_RESTRICTED_DOC`` / ``_RESTRICTED_URI`` in its output."""
    return {
        # get_service / get_repository
        "entity_by_type_name": [
            {
                "id": _ENTITY_ID,
                "entity_type": "service",
                "name": "exec-comp",
                "display_name": "Executive Compensation",
                "document_id": _RESTRICTED_DOC,
                "metadata": {"source_uri": _RESTRICTED_URI},
                "source_type": "confluence",
                "source_uri": _RESTRICTED_URI,
                "title": "exec-comp",
                "container": "HR",
                "deleted_at": None,
            }
        ],
        "entity_neighbours": [
            {"rel_type": "documented_by", "entity_type": "document", "name": "Salary bands"}
        ],
        # find_related_knowledge
        "entity_by_name_any_type": [
            {
                "id": _ENTITY_ID,
                "entity_type": "service",
                "name": "exec-comp",
                "document_id": _RESTRICTED_DOC,
                "source_type": "confluence",
                "source_uri": _RESTRICTED_URI,
                "deleted_at": None,
            }
        ],
        "related_knowledge": [
            {
                "src_name": "exec-comp",
                "dst_name": "salary-bands",
                "rel_type": "documented_by",
                "depth": 1,
                "doc_id": _RESTRICTED_DOC,
                "source_type": "confluence",
                "source_uri": _RESTRICTED_URI,
            }
        ],
        # get_knowledge_summary
        "knowledge_summary": [
            {
                "subject_type": "entity",
                "subject_id": "exec-comp",
                "summary": "executive salary bands 2026 confidential",
                "provenance": [
                    {
                        "source": "confluence",
                        "document_id": _RESTRICTED_DOC,
                        "chunk_id": f"{_RESTRICTED_DOC}#0",
                        "updated_time": "2026-09-30T03:15:00Z",
                        "evidence": {"document_id": _RESTRICTED_DOC, "source_uri": _RESTRICTED_URI},
                    }
                ],
                "generated_at": datetime(2026, 10, 1, tzinfo=UTC),
            }
        ],
        "entity_id_by_name": [{"subject_id": "exec-comp"}],
        "document_citation": [
            {"source_type": "confluence", "source_uri": _RESTRICTED_URI, "title": "exec-comp"}
        ],
        # get_document_version
        "live_document": [
            {
                "document_id": _RESTRICTED_DOC,
                "source_type": "confluence",
                "source_uri": _RESTRICTED_URI,
                "title": "exec-comp salary",
            }
        ],
        "document_versions": [
            {
                "document_id": _RESTRICTED_DOC,
                "version": 3,
                "content_hash": "sha256:restricted",
                "source_version": "3",
                "author": "cfo",
                "source_updated_at": datetime(2026, 9, 30, tzinfo=UTC),
                "created_at": datetime(2026, 10, 1, tzinfo=UTC),
                "status": "current",
            }
        ],
    }


def _restricted_api() -> KnowledgeReadApi:
    """A caller who is a plain team member; the restricted doc is granted only to ``user:cfo``."""
    client = FakeKnowledgeClient(
        data=_restricted_rows(), grants={_RESTRICTED_DOC: {"user:cfo"}}
    )
    candidates = [
        _candidate(_RESTRICTED_DOC, 2, _RESTRICTED_URI, "executive salary bands 2026 confidential")
    ]
    return KnowledgeReadApi(client, FakeRetriever(candidates), caller=CallerContext())


def _assert_no_leak(payload_text: str) -> None:
    assert _RESTRICTED_DOC not in payload_text, "restricted document_id leaked"
    assert _RESTRICTED_URI not in payload_text, "restricted source_uri leaked"


async def test_search_code_denies_restricted_doc() -> None:
    outcome = await _restricted_api().search_code(query="executive salary", top_k=10)
    payload = outcome.result.model_dump(mode="json")
    assert payload["items"] == []
    assert payload["citations"] == []
    _assert_no_leak(str(payload))


async def test_get_service_denies_restricted_doc_as_not_found() -> None:
    outcome = await _restricted_api().get_service(name="exec-comp")
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "not_found"
    _assert_no_leak(str(payload))


async def test_get_repository_denies_restricted_doc_as_not_found() -> None:
    outcome = await _restricted_api().get_repository(name="exec-comp")
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "not_found"
    _assert_no_leak(str(payload))


async def test_find_related_knowledge_denies_restricted_root_as_not_found() -> None:
    outcome = await _restricted_api().find_related_knowledge(entity="exec-comp", max_depth=2)
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "not_found"
    _assert_no_leak(str(payload))


async def test_get_knowledge_summary_denies_restricted_doc_as_not_found() -> None:
    outcome = await _restricted_api().get_knowledge_summary(subject="exec-comp")
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "not_found"
    _assert_no_leak(str(payload))


async def test_get_document_version_denies_restricted_doc_as_not_found() -> None:
    outcome = await _restricted_api().get_document_version(document_id=_RESTRICTED_DOC)
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "not_found"
    assert payload["items"] == []
    assert payload["citations"] == []
    # The restricted source_uri / version content must never appear; the caller's own
    # document_id echoed back in query_echo is their input, not leaked content.
    serialized = str(payload)
    assert _RESTRICTED_URI not in serialized, "restricted source_uri leaked"
    assert "sha256:restricted" not in serialized, "restricted version content_hash leaked"


async def test_search_company_knowledge_denies_restricted_doc() -> None:
    outcome = await _restricted_api().search_company_knowledge(
        query="executive salary bands", top_k=8
    )
    _assert_no_leak(str(outcome.result.model_dump(mode="json")))


async def test_get_jira_context_denies_restricted_doc() -> None:
    outcome = await _restricted_api().get_jira_context(subject="exec-comp", top_k=8)
    _assert_no_leak(str(outcome.result.model_dump(mode="json")))


async def test_find_related_knowledge_keeps_permitted_edges_drops_restricted_edge() -> None:
    # Mixed graph: a permitted root with one permitted edge and one restricted edge. The restricted
    # edge must be dropped; the permitted edge survives. Proves per-edge default-deny, not all-or-
    # nothing, and that a permitted traversal is not broken by the new gate.
    permitted_doc = "66666666-6666-4666-8666-666666666666"
    permitted_uri = "https://wiki.example.com/ok-graph"
    data = {
        "entity_by_name_any_type": [
            {
                "id": _ENTITY_ID,
                "entity_type": "service",
                "name": "root",
                "document_id": permitted_doc,
                "source_type": "confluence",
                "source_uri": permitted_uri,
                "deleted_at": None,
            }
        ],
        "related_knowledge": [
            {
                "src_name": "root",
                "dst_name": "ok-neighbour",
                "rel_type": "depends_on",
                "depth": 1,
                "doc_id": permitted_doc,
                "source_type": "confluence",
                "source_uri": permitted_uri,
            },
            {
                "src_name": "root",
                "dst_name": "secret-neighbour",
                "rel_type": "related_to",
                "depth": 1,
                "doc_id": _RESTRICTED_DOC,
                "source_type": "confluence",
                "source_uri": _RESTRICTED_URI,
            },
        ],
    }
    client = FakeKnowledgeClient(
        data=data, grants={permitted_doc: {TEAM_PRINCIPAL}, _RESTRICTED_DOC: {"user:cfo"}}
    )
    api = KnowledgeReadApi(client, FakeRetriever([]), caller=CallerContext())
    outcome = await api.find_related_knowledge(entity="root", max_depth=2)
    payload = outcome.result.model_dump(mode="json")
    assert payload["status"] == "ok"
    _assert_no_leak(str(payload))
    names = {item["dst"] for item in payload["items"]}
    assert "ok-neighbour" in names
    assert "secret-neighbour" not in names
