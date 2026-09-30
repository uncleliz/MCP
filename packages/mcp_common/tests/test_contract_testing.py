"""mcp_common.contract_testing — the shared snapshot/contract verification helpers."""

from __future__ import annotations

import copy
from typing import Annotated

import pytest
from mcp.server.fastmcp import FastMCP
from mcp_common.contract_testing import (
    assert_snapshot_matches_contract,
    build_snapshot,
    find_contract_path,
    load_contract,
    operations_by_id,
    validate_structured_content,
)
from pydantic import Field

CONTRACT = load_contract()


def _ok_payload() -> dict:
    return {
        "status": "ok",
        "items": [
            {
                "id": "1",
                "title": "T",
                "space_key": "PAY",
                "url": "https://wiki.example.test/x",
                "citation_ref": 0,
            }
        ],
        "citations": [
            {
                "source_type": "confluence",
                "label": "T",
                "uri": "https://wiki.example.test/x",
                "locator": {"page_id": "1", "version": 1},
            }
        ],
        "meta": {
            "source": "confluence",
            "returned": 1,
            "has_more": False,
            "next_cursor": None,
            "truncated": False,
            "elapsed_ms": 3,
            "as_of": "2026-10-01T00:00:00Z",
        },
    }


def test_contract_path_and_operations_found() -> None:
    assert find_contract_path().name == "api-contract.yaml"
    assert "confluence_search_pages" in operations_by_id(CONTRACT)


def test_valid_payload_passes() -> None:
    validate_structured_content(CONTRACT, "confluence_search_pages", _ok_payload())


def test_schema_violation_is_reported() -> None:
    payload = _ok_payload()
    del payload["items"][0]["title"]
    with pytest.raises(AssertionError, match="title"):
        validate_structured_content(CONTRACT, "confluence_search_pages", payload)


def test_non_url_item_url_is_reported() -> None:
    payload = _ok_payload()
    payload["items"][0]["url"] = "/relative"
    with pytest.raises(AssertionError, match="not a URL"):
        validate_structured_content(CONTRACT, "confluence_search_pages", payload)


def test_error_envelope_validation() -> None:
    envelope = {
        "status": "error",
        "error": {
            "code": "upstream_timeout",
            "message": "x",
            "source": "confluence",
            "retryable": True,
        },
    }
    validate_structured_content(CONTRACT, "confluence_search_pages", envelope, is_error=True)
    envelope["error"]["code"] = "nope"
    with pytest.raises(AssertionError):
        validate_structured_content(CONTRACT, "confluence_search_pages", envelope, is_error=True)


@pytest.mark.asyncio
async def test_snapshot_mismatch_is_reported() -> None:
    mcp = FastMCP("x")

    @mcp.tool(name="confluence_list_spaces")
    async def confluence_list_spaces(
        query: str | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,  # wrong maximum on purpose
        cursor: str | None = None,
    ) -> str:
        return ""

    snapshot = await build_snapshot(mcp)
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_matches_contract(snapshot, CONTRACT, tag="confluence")
    message = str(exc.value)
    assert "tool set differs" in message
    assert "confluence_list_spaces.limit" in message


@pytest.mark.asyncio
async def test_snapshot_property_and_required_mismatch() -> None:
    mcp = FastMCP("x")

    @mcp.tool(name="confluence_list_spaces")
    async def confluence_list_spaces(other: str) -> str:
        return ""

    snapshot = await build_snapshot(mcp)
    broken = copy.deepcopy(snapshot)
    with pytest.raises(AssertionError, match="input properties"):
        assert_snapshot_matches_contract(broken, CONTRACT, tag="confluence")
