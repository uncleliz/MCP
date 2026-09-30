"""T-018: Confluence `client.py` — transport, allowlist, startup credential check.

AC: FR-001/AC-003, FR-014/AC-001, NFR-002.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_confluence.client import ALLOWED_OPERATIONS, ConfluenceClient

BASE = "https://acme.atlassian.net/wiki"


@pytest.mark.asyncio
async def test_FR_001_AC_003_get_sends_basic_auth_and_returns_json(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    route = readonly_respx_router.get(f"{BASE}/rest/api/content/search").mock(
        return_value=httpx.Response(200, json=fixture("search_results.json"))
    )
    data = await client.search("type = page", limit=2, start=0, expand="space,version")
    assert len(data["results"]) == 2
    request = route.calls.last.request
    assert request.headers["authorization"].startswith("Basic ")
    assert request.url.params["cql"] == "type = page"
    assert request.url.params["limit"] == "2"
    assert request.url.params["expand"] == "space,version"


@pytest.mark.asyncio
async def test_FR_014_AC_001_operation_outside_allowlist_is_not_permitted(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get("GET /rest/api/content/{id}/child/comment", {"id": "1"})
    assert exc.value.code == ErrorCode.NOT_PERMITTED
    assert exc.value.details["operation"] == "GET /rest/api/content/{id}/child/comment"
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_FR_014_AC_001_write_method_blocked_by_transport_assertion(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.post(f"{BASE}/rest/api/content", json={"title": "x"})
    with pytest.raises(NotPermittedError):
        await client.http.delete(f"{BASE}/rest/api/content/1")
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.parametrize(
    "expand", ["body.export_view", "space,body.export_view,version", "BODY.EXPORT_VIEW"]
)
@pytest.mark.asyncio
async def test_ADR_0007_A1_export_view_rejected_in_code(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, expand: str
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get_content("123", expand=expand)
    assert "export_view" in exc.value.message
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_page_id_is_url_quoted(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    route = readonly_respx_router.get(url__regex=r".*/rest/api/content/.*").mock(
        return_value=httpx.Response(200, json=fixture("page.json"))
    )
    await client.get_content("a/../b", expand="version")
    assert "/rest/api/content/a%2F..%2Fb" in str(route.calls.last.request.url)


@pytest.mark.asyncio
async def test_404_surfaces_upstream_status(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/content/999").mock(
        return_value=httpx.Response(404, json=fixture("error_404.json"))
    )
    with pytest.raises(ToolError) as exc:
        await client.get_content("999", expand="version")
    assert exc.value.details["upstream_status"] == 404


@pytest.mark.asyncio
async def test_non_json_body_is_upstream_error(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/space").mock(
        return_value=httpx.Response(200, text="<html>login</html>")
    )
    with pytest.raises(ToolError) as exc:
        await client.list_spaces(limit=5, start=0)
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


def test_allowlist_is_get_only_and_has_no_export_view() -> None:
    assert all(op.startswith("GET ") for op in ALLOWED_OPERATIONS)
    assert not any("export" in op for op in ALLOWED_OPERATIONS)
    assert "GET /rest/api/user/current" in ALLOWED_OPERATIONS


# ---- startup credential check (ADR-0003 A1 / ADR-0007 A2) -------------------------


def _mock_identity(router: respx.MockRouter, fixture, sample: str) -> None:
    router.get(f"{BASE}/rest/api/user/current").mock(
        return_value=httpx.Response(200, json=fixture("current_user.json"))
    )
    router.get(f"{BASE}/rest/api/content/search").mock(
        return_value=httpx.Response(200, json=fixture(sample))
    )


@pytest.mark.asyncio
async def test_FR_001_AC_003_readonly_account_passes_startup_check(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    _mock_identity(readonly_respx_router, fixture, "sample_page_readonly.json")
    report = await client.verify_credentials()
    assert report.ok, report.reasons
    assert await client.credential_check() is True


@pytest.mark.asyncio
async def test_FR_001_AC_003_account_that_can_write_fails_startup_check(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    _mock_identity(readonly_respx_router, fixture, "sample_page_writable.json")
    report = await client.verify_credentials()
    assert not report.ok
    assert any("create:comment" in r for r in report.reasons)
    assert await client.credential_check() is False


@pytest.mark.asyncio
async def test_unverifiable_permissions_fail_closed(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    _mock_identity(readonly_respx_router, fixture, "sample_page_no_operations.json")
    report = await client.verify_credentials()
    assert not report.ok and "cannot verify" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_no_page_to_sample_fails_closed(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter, fixture
) -> None:
    _mock_identity(readonly_respx_router, fixture, "search_empty.json")
    assert not (await client.verify_credentials()).ok


@pytest.mark.asyncio
async def test_bad_credentials_fail_startup_check_with_reason(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/user/current").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    report = await client.verify_credentials()
    assert not report.ok
    assert "unauthorized" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_anonymous_user_fails_startup_check(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/user/current").mock(
        return_value=httpx.Response(200, json={"type": "anonymous"})
    )
    assert not (await client.verify_credentials()).ok


@pytest.mark.asyncio
async def test_unreachable_host_fails_startup_check_without_raising(
    client: ConfluenceClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{BASE}/rest/api/user/current").mock(
        side_effect=httpx.ConnectError("refused")
    )
    report = await client.verify_credentials()
    assert not report.ok and "VPN" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_serve_refuses_when_startup_check_fails(
    client: ConfluenceClient,
    readonly_respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    fixture,
) -> None:
    from mcp_common.config import CommonSettings, SourceMisconfiguredError
    from mcp_common.runtime import run_credential_check

    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    _mock_identity(readonly_respx_router, fixture, "sample_page_writable.json")
    with pytest.raises(SourceMisconfiguredError):
        await run_credential_check(
            client.credential_check, settings=CommonSettings(), server_name="confluence"
        )
