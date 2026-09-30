"""T-010: mcp_common.http — transport-level read-only assertion (ADR-0003 A2).

Every outbound request must be GET/HEAD unless it matches an explicit
`(host, method, path)` allowlist entry. This is the "assertion ở tầng transport" the
design review substituted for the abandoned deny-regex-by-tool-name approach.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.errors import ErrorCode, NotPermittedError
from mcp_common.http import (
    DEFAULT_TRANSPORT_ALLOWLIST,
    assert_transport_allowed,
    build_client,
)


def test_get_is_always_allowed() -> None:
    assert_transport_allowed("GET", httpx.URL("https://x.test/anything"), allowlist=())


def test_head_is_always_allowed() -> None:
    assert_transport_allowed("HEAD", httpx.URL("https://x.test/anything"), allowlist=())


def test_post_outside_allowlist_is_rejected() -> None:
    with pytest.raises(NotPermittedError) as exc_info:
        assert_transport_allowed("POST", httpx.URL("https://x.test/rest/api/content"), allowlist=())

    assert exc_info.value.code == ErrorCode.NOT_PERMITTED
    assert exc_info.value.retryable is False


def test_post_to_opensearch_search_is_allowed_by_default_allowlist() -> None:
    assert_transport_allowed(
        "POST",
        httpx.URL("https://opensearch.example.test/my-index/_search"),
        allowlist=DEFAULT_TRANSPORT_ALLOWLIST,
    )


def test_post_to_opensearch_count_is_allowed_by_default_allowlist() -> None:
    assert_transport_allowed(
        "POST",
        httpx.URL("https://opensearch.example.test/my-index/_count"),
        allowlist=DEFAULT_TRANSPORT_ALLOWLIST,
    )


def test_post_to_unrelated_path_is_still_rejected_with_default_allowlist() -> None:
    with pytest.raises(NotPermittedError):
        assert_transport_allowed(
            "POST",
            httpx.URL("https://opensearch.example.test/my-index/_delete_by_query"),
            allowlist=DEFAULT_TRANSPORT_ALLOWLIST,
        )


def test_put_and_delete_are_rejected() -> None:
    for method in ("PUT", "DELETE", "PATCH"):
        with pytest.raises(NotPermittedError):
            assert_transport_allowed(method, httpx.URL("https://x.test/y"), allowlist=())


@pytest.mark.respx(base_url="https://confluence.example.test", assert_all_called=False)
@pytest.mark.asyncio
async def test_client_built_via_build_client_blocks_mutating_request_end_to_end(
    respx_mock: respx.MockRouter,
) -> None:
    # Even if a route happens to be mocked, the event hook must reject the request
    # before respx's transport is ever reached for a disallowed method.
    route = respx_mock.post("/rest/api/content").mock(return_value=httpx.Response(200))

    async with build_client() as client:
        with pytest.raises(NotPermittedError):
            await client.post("https://confluence.example.test/rest/api/content", json={})

    assert route.call_count == 0


@pytest.mark.respx(base_url="https://confluence.example.test")
@pytest.mark.asyncio
async def test_client_built_via_build_client_allows_get(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/rest/api/content/search").mock(return_value=httpx.Response(200, json={}))

    async with build_client() as client:
        response = await client.get("https://confluence.example.test/rest/api/content/search")

    assert response.status_code == 200
