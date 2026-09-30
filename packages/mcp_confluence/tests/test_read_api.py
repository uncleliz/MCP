"""T-019: read_api — bounds, CQL building, normalisation, redaction, paging."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import decode_cursor
from mcp_confluence.read_api import ConfluenceReadApi, build_cql

BASE = "https://acme.atlassian.net/wiki"
SEARCH = f"{BASE}/rest/api/content/search"


def test_build_cql_escapes_and_filters() -> None:
    cql = build_cql(
        'say "hi" \\ there',
        space_key="PAY",
        updated_after=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        labels=["a", 'b"c'],
    )
    assert 'text ~ "say \\"hi\\" \\\\ there"' in cql
    assert 'space = "PAY"' in cql
    assert 'lastmodified > "2026-01-02 03:04"' in cql
    assert 'label = "a"' in cql and 'label = "b\\"c"' in cql
    assert cql.startswith("type = page AND ")
    assert cql.endswith("ORDER BY lastmodified DESC")


@pytest.mark.asyncio
async def test_FR_001_AC_001_search_returns_items_citations_and_cursor(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    route = readonly_respx_router.get(SEARCH).mock(
        return_value=httpx.Response(200, json=fixture("search_results.json"))
    )
    outcome = await read_api.search_pages(query="payment retry", limit=2)
    result = outcome.result
    assert result.status.value == "ok"
    assert [i["id"] for i in result.items] == ["123456", "123457"]
    assert [c.locator["page_id"] for c in result.citations] == ["123456", "123457"]
    assert result.meta.has_more and decode_cursor(result.meta.next_cursor, source="c") == {
        "start": 2
    }
    assert result.meta.query_echo["query"] == "payment retry"
    params = route.calls.last.request.url.params
    assert "body.storage" in params["expand"] and "export_view" not in params["expand"]
    excerpt = result.items[0]["excerpt"]
    assert excerpt.startswith('<untrusted-content source="confluence" id="123456">')
    assert "Retry policy" in excerpt


@pytest.mark.asyncio
async def test_search_without_excerpt_skips_body_expand(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    route = readonly_respx_router.get(SEARCH).mock(
        return_value=httpx.Response(200, json=fixture("search_results.json"))
    )
    outcome = await read_api.search_pages(query="x", include_excerpt=False)
    assert "body" not in route.calls.last.request.url.params["expand"]
    assert outcome.result.items[0]["excerpt"] is None


@pytest.mark.asyncio
async def test_search_cursor_is_translated_to_start(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    from mcp_common.tooling import encode_cursor

    route = readonly_respx_router.get(SEARCH).mock(
        return_value=httpx.Response(200, json=fixture("search_empty.json"))
    )
    await read_api.search_pages(query="x", cursor=encode_cursor({"start": 40}))
    assert route.calls.last.request.url.params["start"] == "40"


@pytest.mark.asyncio
async def test_FR_001_AC_002_search_empty_is_a_status_not_an_error(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(SEARCH).mock(
        return_value=httpx.Response(200, json=fixture("search_empty.json"))
    )
    outcome = await read_api.search_pages(query="zzz-nothing", space_key="PAY")
    assert outcome.result.status.value == "empty"
    assert outcome.result.items == [] and outcome.result.citations == []
    assert outcome.result.meta.query_echo["query"] == "zzz-nothing"
    assert outcome.result.meta.query_echo["space_key"] == "PAY"
    assert "tài liệu Confluence" in (outcome.query_description or "")


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"query": ""}, "query"),
        ({"query": "x" * 513}, "query"),
        ({"query": "x", "limit": 0}, "limit"),
        ({"query": "x", "limit": 101}, "limit"),
        ({"query": "x", "labels": ["l"] * 11}, "labels"),
        ({"query": "x", "updated_after": datetime(2026, 1, 1)}, "updated_after"),
        ({"query": "x", "cursor": "!!bad!!"}, "cursor"),
    ],
)
@pytest.mark.asyncio
async def test_bound_violation_is_invalid_input_naming_the_field(
    read_api: ConfluenceReadApi,
    readonly_respx_router: respx.MockRouter,
    kwargs: dict,
    field: str,
) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.search_pages(**kwargs)
    assert exc.value.code == ErrorCode.INVALID_INPUT
    assert exc.value.details["field"] == field
    assert readonly_respx_router.calls.call_count == 0  # never reaches the source


@pytest.mark.asyncio
async def test_get_page_markdown_wrapped_redacted_with_version_locator(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    route = readonly_respx_router.get(f"{BASE}/rest/api/content/123456").mock(
        return_value=httpx.Response(200, json=fixture("page.json"))
    )
    outcome = await read_api.get_page(page_id="123456")
    result = outcome.result
    assert "export_view" not in route.calls.last.request.url.params["expand"]
    assert result.status.value == "ok" and len(result.items) == 1
    content = result.items[0]["content"]
    assert content.startswith('<untrusted-content source="confluence" id="123456">')
    assert "# Retry policy" in content
    assert "Human\u200b:" in content  # fake conversation framing is neutralised (ADR-0015)
    assert result.items[0]["excerpt"] is None
    assert result.citations[0].locator == {"page_id": "123456", "version": 7}


@pytest.mark.asyncio
async def test_get_page_text_format(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/123456").mock(
        return_value=httpx.Response(200, json=fixture("page.json"))
    )
    outcome = await read_api.get_page(page_id="123456", body_format="text")
    assert "# Retry policy" not in outcome.result.items[0]["content"]


@pytest.mark.asyncio
async def test_get_page_redacts_secrets_and_counts(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/123999").mock(
        return_value=httpx.Response(200, json=fixture("page_with_secret.json"))
    )
    outcome = await read_api.get_page(page_id="123999")
    assert "glpat-" not in outcome.result.items[0]["content"]
    assert outcome.result.meta.redactions >= 1


@pytest.mark.asyncio
async def test_get_page_truncates_at_max_chars_and_is_partial(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    raw = fixture("page.json")
    raw["body"]["storage"]["value"] = "<p>" + ("word " * 1000) + "</p>"
    readonly_respx_router.get(f"{BASE}/rest/api/content/123456").mock(
        return_value=httpx.Response(200, json=raw)
    )
    outcome = await read_api.get_page(page_id="123456", max_chars=500)
    assert outcome.result.status.value == "partial"
    assert outcome.result.meta.truncated is True
    assert outcome.result.citations  # partial still carries citations


@pytest.mark.asyncio
async def test_FR_001_AC_002_unknown_page_is_not_found_status(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/999").mock(
        return_value=httpx.Response(404, json=fixture("error_404.json"))
    )
    outcome = await read_api.get_page(page_id="999")
    assert outcome.result.status.value == "not_found"
    assert outcome.identifier == "Page 999"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"page_id": "bad id!"}, "page_id"),
        ({"page_id": "1", "max_chars": 10}, "max_chars"),
        ({"page_id": "1", "max_chars": 100001}, "max_chars"),
        ({"page_id": "1", "body_format": "html"}, "body_format"),
    ],
)
@pytest.mark.asyncio
async def test_get_page_bounds(read_api: ConfluenceReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_page(**kwargs)
    assert exc.value.details["field"] == field


@pytest.mark.asyncio
async def test_list_spaces_and_filter(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/space").mock(
        return_value=httpx.Response(200, json=fixture("spaces.json"))
    )
    all_spaces = await read_api.list_spaces()
    assert [s["key"] for s in all_spaces.result.items] == ["PAY", "~dana", "KB"]
    filtered = await read_api.list_spaces(query="pay")
    assert [s["key"] for s in filtered.result.items] == ["PAY"]
    none = await read_api.list_spaces(query="zzz")
    assert none.result.status.value == "empty"


@pytest.mark.asyncio
async def test_list_page_children_paths(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/100/child/page").mock(
        return_value=httpx.Response(200, json=fixture("children.json"))
    )
    readonly_respx_router.get(f"{BASE}/rest/api/content/101/child/page").mock(
        return_value=httpx.Response(200, json=fixture("children_empty.json"))
    )
    readonly_respx_router.get(f"{BASE}/rest/api/content/404/child/page").mock(
        return_value=httpx.Response(404, json=fixture("error_404.json"))
    )
    ok = await read_api.list_page_children(page_id="100")
    assert [i["id"] for i in ok.result.items] == ["200", "201"]
    assert (await read_api.list_page_children(page_id="101")).result.status.value == "empty"
    assert (await read_api.list_page_children(page_id="404")).result.status.value == "not_found"


@pytest.mark.asyncio
async def test_other_upstream_errors_propagate(
    read_api: ConfluenceReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/5").mock(
        return_value=httpx.Response(403, json={})
    )
    with pytest.raises(ToolError) as exc:
        await read_api.get_page(page_id="5")
    assert exc.value.code == ErrorCode.FORBIDDEN
