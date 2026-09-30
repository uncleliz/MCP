"""T-014: mcp_common.testing — shared verification fixtures.

Done-when (implementation-plan.md): the fixture catches 3 deliberate violations
(a POST tool outside the allowlist, an operation missing x-readonly, a tool present
in the snapshot but absent from the contract) and the unknown-tool helper asserts
the correct protocol-layer rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp_common.envelope import Citation, Meta, ResultStatus, SourceType, ToolResult
from mcp_common.http import build_client
from mcp_common.readonly import readonly_tool
from mcp_common.testing import (
    assert_all_calls_readonly,
    assert_envelope_invariants,
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
    readonly_respx_router,
    warn_if_tool_name_matches_deny_regex,
)

# Re-exported so pytest can discover it as a fixture by name in this module.
__all__ = ["readonly_respx_router"]


def _op(*, readonly: bool = True, side_effects: str = "none", interface: str | None = None) -> dict:
    op: dict = {}
    if readonly:
        op["x-readonly"] = True
    op["x-side-effects"] = side_effects
    if interface:
        op["x-interface"] = interface
    return op


# ---------------------------------------------------------------------------
# assert_readonly_tool_surface — 3 deliberate violations
# ---------------------------------------------------------------------------


def test_clean_surface_passes() -> None:
    @readonly_tool
    async def confluence_search_pages() -> dict:
        return {}

    registered = {"confluence_search_pages": confluence_search_pages}
    contract = {"confluence_search_pages": _op()}
    snapshot = {"confluence_search_pages": {}}

    assert_readonly_tool_surface(
        registered_tools=registered, contract_operations=contract, snapshot=snapshot
    )


def test_unmarked_tool_function_is_caught() -> None:
    async def confluence_search_pages() -> dict:  # missing @readonly_tool
        return {}

    registered = {"confluence_search_pages": confluence_search_pages}
    contract = {"confluence_search_pages": _op()}

    with pytest.raises(AssertionError, match="not marked @readonly_tool"):
        assert_readonly_tool_surface(
            registered_tools=registered, contract_operations=contract, snapshot={}
        )


def test_operation_missing_x_readonly_is_caught() -> None:
    @readonly_tool
    async def gitlab_search_code() -> dict:
        return {}

    registered = {"gitlab_search_code": gitlab_search_code}
    # Deliberate violation #1: a "POST" operation missing x-readonly entirely.
    contract = {"gitlab_search_code": _op(readonly=False)}

    with pytest.raises(AssertionError, match="x-readonly"):
        assert_readonly_tool_surface(
            registered_tools=registered, contract_operations=contract, snapshot={}
        )


def test_operation_missing_x_side_effects_is_caught() -> None:
    @readonly_tool
    async def redis_get_key() -> dict:
        return {}

    registered = {"redis_get_key": redis_get_key}
    # Deliberate violation #2: operation missing x-side-effects: none.
    contract = {"redis_get_key": {"x-readonly": True, "x-side-effects": "writes"}}

    with pytest.raises(AssertionError, match="x-side-effects"):
        assert_readonly_tool_surface(
            registered_tools=registered, contract_operations=contract, snapshot={}
        )


def test_snapshot_entry_not_in_contract_is_caught() -> None:
    contract = {"confluence_search_pages": _op()}
    # Deliberate violation #3: a tool present in the snapshot but absent from the contract.
    snapshot = {"confluence_search_pages": {}, "confluence_delete_page": {}}

    with pytest.raises(AssertionError, match="confluence_delete_page"):
        assert_readonly_tool_surface(
            registered_tools={}, contract_operations=contract, snapshot=snapshot
        )


def test_cli_only_operations_are_excluded_from_snapshot_subset_check() -> None:
    contract = {
        "confluence_search_pages": _op(),
        "ingest_run": _op(readonly=False, side_effects="writes:kb", interface="cli"),
    }
    snapshot = {"confluence_search_pages": {}}  # ingest_run correctly absent

    assert_readonly_tool_surface(
        registered_tools={}, contract_operations=contract, snapshot=snapshot
    )


def test_additional_client_operations_not_in_client_allowlist_is_caught() -> None:
    # ADR-0012 A4: methods mcp-ingest's connector calls on client.py must also be allowlisted.
    with pytest.raises(AssertionError, match="mcp-ingest-reused"):
        assert_readonly_tool_surface(
            registered_tools={},
            contract_operations={},
            snapshot={},
            client_allowlist=["GET /rest/api/content/search"],
            additional_client_operations=["GET /rest/api/content/search", "POST /rest/api/content"],
        )


# ---------------------------------------------------------------------------
# warn_if_tool_name_matches_deny_regex — warning only, not a failure
# ---------------------------------------------------------------------------


def test_deny_regex_false_positives_on_merge_request_tools_but_is_informational_only() -> None:
    names = ["gitlab_list_merge_requests", "gitlab_get_merge_request", "confluence_search_pages"]
    matches = warn_if_tool_name_matches_deny_regex(names)
    assert "gitlab_list_merge_requests" in matches
    assert "confluence_search_pages" not in matches
    # The function itself never raises — callers decide whether to log a warning.


# ---------------------------------------------------------------------------
# assert_envelope_invariants
# ---------------------------------------------------------------------------


def _meta(**overrides) -> Meta:
    base = dict(
        source=SourceType.CONFLUENCE,
        returned=1,
        has_more=False,
        truncated=False,
        elapsed_ms=1,
        as_of=datetime(2026, 10, 1, tzinfo=UTC),
    )
    base.update(overrides)
    return Meta(**base)


def test_envelope_invariants_pass_for_well_formed_result() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://x")],
        meta=_meta(),
    )
    assert_envelope_invariants(result)


def test_envelope_invariants_catch_dangling_citation_ref() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 5}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://x")],
        meta=_meta(),
    )
    with pytest.raises(AssertionError, match="out of bounds"):
        assert_envelope_invariants(result)


def test_envelope_invariants_catch_returned_mismatch() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://x")],
        meta=_meta(returned=99),
    )
    with pytest.raises(AssertionError, match="meta.returned"):
        assert_envelope_invariants(result)


def test_envelope_invariants_catch_missing_uri_for_url_required_source() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[Citation(source_type=SourceType.GITLAB, label="x", uri=None)],
        meta=_meta(source=SourceType.GITLAB),
    )
    with pytest.raises(AssertionError, match="always resolves to a URL"):
        assert_envelope_invariants(result)


def test_envelope_invariants_allow_null_uri_for_sourceless_types() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[
            Citation(source_type=SourceType.KAFKA, label="x", uri=None, locator={"topic": "t"})
        ],
        meta=_meta(source=SourceType.KAFKA),
    )
    assert_envelope_invariants(result)


# ---------------------------------------------------------------------------
# assert_unknown_tool_rejected_at_protocol_layer — FR-014 AC-002
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_at_protocol_layer() -> None:
    server = FastMCP(name="test-server")

    @server.tool()
    async def real_tool(x: str) -> str:
        return x

    await assert_unknown_tool_rejected_at_protocol_layer(server, "confluence_delete_page")


@pytest.mark.asyncio
async def test_registered_tool_does_not_trip_the_unknown_tool_assertion() -> None:
    server = FastMCP(name="test-server")

    @server.tool()
    async def real_tool(x: str) -> str:
        return x

    with pytest.raises(AssertionError, match="expected calling unregistered tool"):
        await assert_unknown_tool_rejected_at_protocol_layer(
            server, "real_tool", arguments={"x": "hello"}
        )


# ---------------------------------------------------------------------------
# readonly_respx_router fixture / assert_all_calls_readonly
# ---------------------------------------------------------------------------


@pytest.mark.respx(base_url="https://x.test", assert_all_called=False)
@pytest.mark.asyncio
async def test_assert_all_calls_readonly_passes_for_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/ok").mock(return_value=httpx.Response(200))
    async with build_client() as client:
        await client.get("https://x.test/ok")

    assert_all_calls_readonly(respx_mock)


@pytest.mark.asyncio
async def test_assert_all_calls_readonly_catches_disallowed_post_via_raw_client() -> None:
    # Build a raw httpx client WITHOUT the build_client() guard, to prove
    # assert_all_calls_readonly (not just build_client's event hook) is what catches
    # the violation — this is the mechanism `readonly_respx_router`'s teardown uses.
    with respx.mock(assert_all_called=False) as router:
        router.post("https://x.test/rest/api/content").mock(return_value=httpx.Response(200))

        async with httpx.AsyncClient() as raw_client:
            await raw_client.post("https://x.test/rest/api/content", json={})

        with pytest.raises(AssertionError, match="Disallowed requests"):
            assert_all_calls_readonly(router)


@pytest.mark.asyncio
async def test_readonly_respx_router_fixture_allows_a_clean_get(
    readonly_respx_router: respx.MockRouter,
) -> None:
    readonly_respx_router.get("https://x.test/ok").mock(return_value=httpx.Response(200))

    async with httpx.AsyncClient() as raw_client:
        response = await raw_client.get("https://x.test/ok")

    assert response.status_code == 200
    # No AssertionError at fixture teardown means this test genuinely passed.
