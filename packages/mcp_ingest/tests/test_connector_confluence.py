"""T-070: Confluence connector over the real `mcp_confluence.client` with a mocked HTTP layer.

AC: FR-012/AC-001, BR-005. Fixtures are hand-written to the shape of the Confluence Cloud REST API
(no live tenant reachable, spike S1): `@live` tests in mcp_confluence cover the real shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_confluence.client import ALLOWED_OPERATIONS, ConfluenceClient
from mcp_confluence.settings import Settings as ConfluenceSettings
from mcp_ingest.connectors import confluence as module
from mcp_ingest.connectors.base import Cursor
from mcp_ingest.connectors.confluence import PAGE_SIZE, ConfluenceConnector
from mcp_ingest.settings import Settings
from pydantic import SecretStr

BASE = "https://acme.atlassian.net/wiki"
SEARCH = f"{BASE}/rest/api/content/search"


def page(
    page_id: str,
    *,
    space: str = "PAY",
    space_type: str = "global",
    when: str = "2026-09-30T10:00:00.000Z",
    restricted: bool | None = False,
    ancestors: list[str] | None = None,
    title: str = "Payment retry policy",
    body: str = "<h1>Retry</h1><p>Three attempts.</p>",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": page_id, "type": "page", "title": title,
        "space": {"key": space, "type": space_type},
        "version": {"number": 4, "when": when, "by": {"displayName": "An Nguyen"}},
        "body": {"storage": {"value": body}},
        "ancestors": [{"id": a} for a in ancestors or []],
        "_links": {"webui": f"/spaces/{space}/pages/{page_id}/Retry", "base": BASE},
    }  # fmt: skip
    if restricted is not None:
        users = [{"accountId": "u1"}] if restricted else []
        node["restrictions"] = {
            "read": {"restrictions": {"user": {"results": users}, "group": {"results": []}}}
        }
    return node


def restriction_only(page_id: str, restricted: bool) -> dict[str, Any]:
    return {"id": page_id, "restrictions": page(page_id, restricted=restricted)["restrictions"]}


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def connector(router) -> ConfluenceConnector:
    client = ConfluenceClient(
        ConfluenceSettings(base_url=BASE, email="svc@acme.test", api_token=SecretStr("not-real")),
        common=CommonSettings(http_backoff_base=0.0),
    )
    conn = ConfluenceConnector(client, team_spaces=["PAY", "OPS"])
    yield conn
    conn.close()


def serve_pages(router, pages: list[dict[str, Any]]) -> respx.Route:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", "0"))
        chunk = pages[start : start + PAGE_SIZE]
        more = start + PAGE_SIZE < len(pages)
        return httpx.Response(200, json={"results": chunk, "_links": {"next": "x"} if more else {}})

    return router.get(SEARCH).mock(side_effect=handler)


def test_FR_012_AC_001_crawl_yields_documents_with_full_citation_metadata(
    router, connector
) -> None:
    serve_pages(router, [page("123456")])
    (document,) = list(connector.iter_documents(None, "full"))
    assert document.source_type == "confluence" and document.source_id == "123456"
    assert document.source_uri == f"{BASE}/spaces/PAY/pages/123456/Retry"  # the original URL
    assert document.title == "Payment retry policy" and document.container == "PAY"
    assert document.author == "An Nguyen" and document.source_format == "html"
    assert document.source_updated_at == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert "Three attempts" in document.raw_content and document.visibility == "team"


def test_visibility_follows_the_s5_table(router, connector) -> None:
    router.get(f"{BASE}/rest/api/content/900").mock(
        return_value=httpx.Response(200, json=restriction_only("900", True))
    )
    router.get(f"{BASE}/rest/api/content/901").mock(
        return_value=httpx.Response(200, json=restriction_only("901", False))
    )
    pages = [
        page("1"),                                        # plain team page
        page("2", restricted=True),                       # page has a read restriction
        page("3", space="HR"),                            # space not declared team-wide
        page("4", space_type="personal"),                 # personal space
        page("5", restricted=None),                       # restrictions not reported => unknown
        page("6", ancestors=["901"]),                     # unrestricted ancestor
        page("7", ancestors=["901", "900"]),              # restriction INHERITED from an ancestor
    ]  # fmt: skip
    serve_pages(router, pages)
    labels = {d.source_id: d.visibility for d in connector.iter_documents(None, "full")}
    assert labels == {
        "1": "team", "2": "restricted", "3": "restricted", "4": "restricted",
        "5": "restricted", "6": "team", "7": "restricted",
    }  # fmt: skip


def test_an_ancestor_that_cannot_be_read_means_default_deny(router, connector) -> None:
    router.get(f"{BASE}/rest/api/content/950").mock(return_value=httpx.Response(403, json={}))
    serve_pages(router, [page("1", ancestors=["950"])])
    (document,) = list(connector.iter_documents(None, "full"))
    assert document.visibility == "restricted"


def test_ancestor_lookups_are_cached(router, connector) -> None:
    ancestor = router.get(f"{BASE}/rest/api/content/901").mock(
        return_value=httpx.Response(200, json=restriction_only("901", False))
    )
    serve_pages(router, [page(str(i), ancestors=["901"]) for i in range(1, 6)])
    assert len(list(connector.iter_documents(None, "full"))) == 5
    assert ancestor.call_count == 1


def test_pagination_walks_every_page_and_limit_stops_early(router, connector) -> None:
    serve_pages(router, [page(str(i)) for i in range(1, 2 * PAGE_SIZE + 5)])
    assert len(list(connector.iter_documents(None, "full"))) == 2 * PAGE_SIZE + 4
    assert len(list(connector.iter_documents(None, "full", limit=3))) == 3


def test_the_cql_is_scoped_to_team_spaces_and_the_watermark_boundary_is_inclusive(
    router, connector
) -> None:
    on_boundary = page("1", when="2026-09-30T10:00:00.000Z")
    older = page("2", when="2026-09-30T09:59:59.000Z")
    newer = page("3", when="2026-09-30T10:00:01.000Z")
    route = serve_pages(router, [older, on_boundary, newer])
    cursor = Cursor(datetime(2026, 9, 30, 10, 0, tzinfo=UTC))

    ids = [d.source_id for d in connector.iter_documents(cursor, "incremental")]

    assert ids == ["1", "3"], ">= the watermark: the boundary document is included, older is not"
    cql = route.calls.last.request.url.params["cql"]
    assert 'space in ("PAY", "OPS")' in cql and "type = page" in cql
    assert 'lastModified >= "2026-09-29 10:00"' in cql  # widened by the timezone-skew guard
    assert cql.endswith("ORDER BY lastModified ASC")

    full = [d.source_id for d in connector.iter_documents(cursor, "full")]
    assert (
        full == ["2", "1", "3"]
        and "lastModified >=" not in route.calls.last.request.url.params["cql"]
    )


def test_retry_after_is_respected_on_a_429(router, connector) -> None:
    route = router.get(SEARCH).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={}),
            httpx.Response(200, json={"results": [page("1")], "_links": {}}),
        ]
    )
    assert [d.source_id for d in connector.iter_documents(None, "full")] == ["1"]
    assert route.call_count == 2


def test_fetch_documents_skips_pages_deleted_at_the_source(router, connector) -> None:
    router.get(f"{BASE}/rest/api/content/10").mock(
        return_value=httpx.Response(200, json=page("10"))
    )
    router.get(f"{BASE}/rest/api/content/11").mock(return_value=httpx.Response(404, json={}))
    assert [d.source_id for d in connector.fetch_documents(["10", "11"])] == ["10"]


def test_only_allowlisted_get_operations_are_used(router, connector) -> None:
    serve_pages(router, [page("1", ancestors=["901"])])
    router.get(f"{BASE}/rest/api/content/901").mock(
        return_value=httpx.Response(200, json=restriction_only("901", False))
    )
    list(connector.iter_documents(None, "full"))
    assert {call.request.method for call in router.calls} == {"GET"}
    assert set(ConfluenceConnector.operations_used) <= set(ALLOWED_OPERATIONS)
    assert not any("export_view" in str(call.request.url) for call in router.calls)


def test_a_non_numeric_page_id_is_rejected_by_the_identity_rules(router, connector) -> None:
    serve_pages(router, [page("not-a-number")])
    with pytest.raises(ValueError, match="numeric"):
        list(connector.iter_documents(None, "full"))


def test_status_and_build_follow_the_environment(monkeypatch) -> None:
    for name in ("MCP_CONFLUENCE_BASE_URL", "MCP_CONFLUENCE_EMAIL", "MCP_CONFLUENCE_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MCP_INGEST_CONFLUENCE_TEAM_SPACES", raising=False)
    status = module.status(Settings())
    assert status.enabled and not status.configured
    assert "MCP_INGEST_CONFLUENCE_TEAM_SPACES" in status.missing_env
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc@acme.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN", "not-real")
    monkeypatch.setenv("MCP_INGEST_CONFLUENCE_TEAM_SPACES", "PAY,OPS")
    assert module.status(Settings()).configured
    built = module.build(Settings())
    try:
        assert built.status().configured and built.source_type == "confluence"
    finally:
        built.close()
