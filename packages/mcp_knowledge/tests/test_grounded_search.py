"""T-102: `search_company_knowledge` / `get_jira_context` grounded envelope (ADR-0018, FR-016).

The E4 pipeline has no verdict gate yet, so the invariant asserted here is the one independent of
the (TBD) threshold: no claim is a FACT, no source-less claim is fabricated, and the result is a
contract-valid `GroundedResult` with `status=insufficient_evidence` + the fixed UNKNOWN message
(GT-1 / TC-095 / TC-079).
"""

from __future__ import annotations

from typing import Any

import pytest
from knowledge_helpers import FakeKnowledgeClient, FakeRetriever, make_candidate
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.envelope import UNKNOWN_MESSAGE, GroundedResult, GroundedStatus, GroundingVerdict
from mcp_common.errors import ErrorCode, ToolError
from mcp_knowledge.server import build_server
from mcp_knowledge.tools.read_api import KnowledgeReadApi

CONTRACT = load_contract()
pytestmark = pytest.mark.asyncio


def _resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        target = CONTRACT
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def validate_grounded(operation_id: str, payload: dict) -> None:
    """Validate a grounded tool's structuredContent against the contract's 200 schema.

    `mcp_common.contract_testing.validate_structured_content` assumes a `ToolResult` (items/…), so
    for the grounded envelope we validate the JSON Schema directly and re-parse with the pydantic
    `GroundedResult` to exercise the construction-time invariants (GT-1/3/4/5)."""
    from jsonschema import Draft202012Validator

    response = _resolve(operations_by_id(CONTRACT)[operation_id]["responses"]["200"])
    schema = response["content"]["application/json"]["schema"]
    root = {"components": CONTRACT["components"], **schema}
    errors = sorted(
        Draft202012Validator(root).iter_errors(payload), key=lambda e: list(e.absolute_path)
    )
    assert not errors, "\n".join(
        f"- {'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:10]
    )
    GroundedResult.model_validate(payload)


def _api(candidates=None, *, jira=None, reranker=None) -> KnowledgeReadApi:
    return KnowledgeReadApi(
        FakeKnowledgeClient(), FakeRetriever(candidates or []), reranker=reranker, jira=jira
    )


async def _call(server, name: str, args: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as session:
        return await session.call_tool(name, arguments=args)


async def test_search_company_knowledge_with_evidence_is_graded_fact_not_fabricated() -> None:
    # E8: an evidenced candidate is graded FACT by the single grounding gate (not fabricated — the
    # verdict is server-assigned deterministically from resolvable provenance).
    res = await _call(
        build_server(_api([make_candidate()])),
        "search_company_knowledge", {"query": "connection timeout payment-service"},
    )
    assert not res.isError, res.content
    validate_grounded("search_company_knowledge", res.structuredContent)
    payload = res.structuredContent
    assert payload["status"] == GroundedStatus.OK.value
    assert payload["grounding_summary"]["fact"] >= 1
    assert payload["grounding_summary"]["calibration_status"] == "uncalibrated"
    facts = [c for c in payload["claims"] if c["grounding"] == GroundingVerdict.FACT.value]
    assert facts and facts[0]["provenance"], "a FACT must carry provenance (never fabricated)"


async def test_search_company_knowledge_no_evidence_still_unknown() -> None:
    res = await _call(
        build_server(_api([])),
        "search_company_knowledge", {"query": "off-topic question with no corpus hit"},
    )
    validate_grounded("search_company_knowledge", res.structuredContent)
    payload = res.structuredContent
    assert payload["status"] == GroundedStatus.INSUFFICIENT_EVIDENCE.value
    assert payload["claims"][0]["grounding"] == GroundingVerdict.UNKNOWN.value
    assert payload["claims"][0]["message"] == UNKNOWN_MESSAGE
    assert payload["claims"][0]["provenance"] == []


async def test_get_jira_context_without_jira_warns_but_validates() -> None:
    res = await _call(build_server(_api([make_candidate()])), "get_jira_context",
                      {"subject": "payment-service"})  # fmt: skip
    assert not res.isError
    validate_grounded("get_jira_context", res.structuredContent)
    assert any("Jira" in w for w in res.structuredContent["meta"]["warnings"])


async def test_get_jira_context_live_error_degrades_to_warning() -> None:
    class _Jira:
        async def search_issues(self, jql: str, *, max_results: int) -> Any:
            raise ToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "down", "jira", True)

    res = await _call(
        build_server(_api([make_candidate()], jira=_Jira())),
        "get_jira_context", {"subject": "payment-service"},
    )
    assert not res.isError
    payload = res.structuredContent
    validate_grounded("get_jira_context", payload)
    assert any("live Jira unavailable" in w for w in payload["meta"]["warnings"])


async def test_reranker_disabled_is_reported_transparently() -> None:
    from mcp_knowledge.rerank.local import Reranker

    res = await _call(
        build_server(_api([make_candidate()], reranker=Reranker(enabled=False))),
        "search_company_knowledge", {"query": "payment retry policy"},
    )
    payload = res.structuredContent
    assert payload["grounding_summary"]["reranker"] == "disabled"
    assert payload["grounding_summary"]["calibration_status"] == "uncalibrated"


async def test_grounded_tool_error_becomes_error_envelope_not_grounded() -> None:
    # A ToolError raised inside a grounded tool must surface as an ErrorEnvelope at the boundary
    # (register_grounded_tool error path), never as a half-built GroundedResult.
    class _BoomRetriever(FakeRetriever):
        async def retrieve(self, query):  # type: ignore[override]
            raise ToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "db down", "knowledge", True)

    api = KnowledgeReadApi(FakeKnowledgeClient(), _BoomRetriever([]))
    res = await _call(build_server(api), "search_company_knowledge", {"query": "anything here"})
    assert res.isError
    assert res.structuredContent["error"]["code"] == "upstream_unavailable"
    assert res.structuredContent["error"]["source"] == "knowledge"
