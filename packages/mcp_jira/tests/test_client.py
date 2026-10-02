"""T-091 / T-094: Jira `client.py` — transport, allowlist, Cloud/Server flavor split,
opaque cursor, startup read-only credential check.

AC: FR-017/AC-003, FR-014/AC-001, NFR-006 (ADR-0019 / ADR-0003 / ADR-0007).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_jira.client import ALLOWED_OPERATIONS, JiraClient

from .conftest import make_settings
from .jira_helpers import (
    board_sprints_url,
    issue_url,
    myself_url,
    project_search_url,
    project_url,
    search_url,
)


def test_allowlist_is_get_only_and_version_independent() -> None:
    assert all(op.startswith("GET ") for op in ALLOWED_OPERATIONS)
    # The allowlist keys never pin a concrete API version (ADR-0019: one allowlist, two flavors).
    assert not any("/rest/api/2/" in op or "/rest/api/3/" in op for op in ALLOWED_OPERATIONS)
    assert "GET /rest/api/myself" in ALLOWED_OPERATIONS


def test_source_never_calls_a_write_verb_on_http() -> None:
    import re
    from pathlib import Path

    import mcp_jira

    pattern = re.compile(r"\.(post|put|delete|patch)\(|\"(POST|PUT|DELETE|PATCH)\"")
    offenders = []
    for path in Path(mcp_jira.__file__).parent.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []


@pytest.mark.asyncio
async def test_FR_017_AC_003_operation_outside_allowlist_is_not_permitted(
    client: JiraClient, readonly_respx_router: respx.MockRouter
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get("GET /rest/api/issue/{key}/transitions", {"key": "PAY-1"})
    assert exc.value.code == ErrorCode.NOT_PERMITTED
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_FR_014_AC_001_transport_blocks_every_write_method(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, method: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{base}/rest/api/2/issue")
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_flavor_hits_correct_api_version_and_auth(
    client: JiraClient,
    settings_flavor: tuple,
    readonly_respx_router: respx.MockRouter,
    fixture,
    base: str,
    flavor: str,
) -> None:
    route = readonly_respx_router.get(issue_url(base, flavor, "PAY-1234")).mock(
        return_value=httpx.Response(200, json=fixture("issue.json"))
    )
    await client.get_issue("PAY-1234")
    request = route.calls.last.request
    if flavor == "cloud":
        assert request.headers["authorization"].startswith("Basic ")
    else:
        assert request.headers["authorization"] == "Bearer pat-not-a-real-secret"


@pytest.mark.asyncio
async def test_cloud_cursor_is_next_page_token(
    readonly_respx_router: respx.MockRouter, fixture, common
) -> None:
    settings = make_settings("cloud")
    client = JiraClient(settings, common=common)
    url = search_url(settings.base_url, "cloud")
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(200, json=fixture("search_cloud.json"))
    )
    page = await client.search_issues("project = PAY", max_results=1)
    assert page.next_cursor_state == {"nextPageToken": "CURSOR-PAGE-2"}
    # Resuming forwards the opaque token, not an offset.
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(200, json={"issues": [], "total": 5})
    )
    await client.search_issues(
        "project = PAY", max_results=1, cursor_state={"nextPageToken": "CURSOR-PAGE-2"}
    )
    assert readonly_respx_router.calls.last.request.url.params["nextPageToken"] == "CURSOR-PAGE-2"
    await client.aclose()


@pytest.mark.asyncio
async def test_server_cursor_is_start_at_offset(
    readonly_respx_router: respx.MockRouter, fixture, common
) -> None:
    settings = make_settings("server")
    client = JiraClient(settings, common=common)
    url = search_url(settings.base_url, "server")
    # total=2, one issue returned at startAt=0 => next offset 1.
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(200, json=fixture("search_server.json"))
    )
    page = await client.search_issues("project = PAY", max_results=1)
    assert page.next_cursor_state == {"startAt": 1}
    assert "nextPageToken" not in str(page.next_cursor_state)
    await client.aclose()


@pytest.mark.asyncio
async def test_server_list_projects_handles_bare_array(
    readonly_respx_router: respx.MockRouter, fixture, common
) -> None:
    settings = make_settings("server")
    client = JiraClient(settings, common=common)
    readonly_respx_router.get(project_url(settings.base_url, "server")).mock(
        return_value=httpx.Response(200, json=fixture("projects_server.json"))
    )
    page = await client.list_projects(max_results=20)
    assert [p["key"] for p in page.values] == ["PAY"]
    assert page.next_cursor_state is None
    await client.aclose()


@pytest.mark.asyncio
async def test_404_surfaces_upstream_status(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, flavor: str
) -> None:
    readonly_respx_router.get(issue_url(base, flavor, "ZZZ-9999")).mock(
        return_value=httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
    )
    with pytest.raises(ToolError) as exc:
        await client.get_issue("ZZZ-9999")
    assert exc.value.details["upstream_status"] == 404


@pytest.mark.asyncio
async def test_non_json_body_is_upstream_error(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, flavor: str
) -> None:
    readonly_respx_router.get(myself_url(base, flavor)).mock(
        return_value=httpx.Response(200, text="<html>login</html>")
    )
    with pytest.raises(ToolError) as exc:
        await client.current_user()
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


# ---- startup credential check (ADR-0003 A1 / ADR-0007 A2 / ADR-0019) -----------------


def _mock_identity(router, fixture, base, flavor, probe: str) -> None:
    router.get(myself_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("myself.json"))
    )
    router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("issue_sample.json"))
    )
    router.get(issue_url(base, flavor, "PAY-1234")).mock(
        return_value=httpx.Response(200, json=fixture(probe))
    )


@pytest.mark.asyncio
async def test_FR_017_AC_003_readonly_account_passes_startup_check(
    client: JiraClient, readonly_respx_router: respx.MockRouter, fixture, base: str, flavor: str
) -> None:
    _mock_identity(readonly_respx_router, fixture, base, flavor, "issue_probe_readonly.json")
    report = await client.verify_credentials()
    assert report.ok, report.reasons
    assert await client.credential_check() is True


@pytest.mark.asyncio
async def test_FR_017_AC_003_account_that_can_write_fails_startup_check(
    client: JiraClient, readonly_respx_router: respx.MockRouter, fixture, base: str, flavor: str
) -> None:
    _mock_identity(readonly_respx_router, fixture, base, flavor, "issue_probe_writable.json")
    report = await client.verify_credentials()
    assert not report.ok
    assert any("not read-only" in r for r in report.reasons)
    assert await client.credential_check() is False


@pytest.mark.asyncio
async def test_no_issue_to_sample_fails_closed(
    client: JiraClient, readonly_respx_router: respx.MockRouter, fixture, base: str, flavor: str
) -> None:
    readonly_respx_router.get(myself_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("myself.json"))
    )
    readonly_respx_router.get(search_url(base, flavor)).mock(
        return_value=httpx.Response(200, json=fixture("search_empty.json"))
    )
    report = await client.verify_credentials()
    assert not report.ok and "no issue visible" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_bad_credentials_fail_startup_check_with_reason(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, flavor: str
) -> None:
    readonly_respx_router.get(myself_url(base, flavor)).mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    report = await client.verify_credentials()
    assert not report.ok and "unauthorized" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_unreachable_host_fails_startup_check_without_raising(
    client: JiraClient, readonly_respx_router: respx.MockRouter, base: str, flavor: str
) -> None:
    readonly_respx_router.get(myself_url(base, flavor)).mock(
        side_effect=httpx.ConnectError("refused")
    )
    report = await client.verify_credentials()
    assert not report.ok


@pytest.mark.asyncio
async def test_serve_refuses_when_startup_check_fails(
    client: JiraClient,
    readonly_respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    fixture,
    base: str,
    flavor: str,
) -> None:
    from mcp_common.config import CommonSettings, SourceMisconfiguredError
    from mcp_common.runtime import run_credential_check

    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    _mock_identity(readonly_respx_router, fixture, base, flavor, "issue_probe_writable.json")
    with pytest.raises(SourceMisconfiguredError):
        await run_credential_check(
            client.credential_check, settings=CommonSettings(), server_name="jira"
        )


# A dummy fixture referenced by test signatures that take `settings_flavor` for symmetry.
@pytest.fixture
def settings_flavor(settings) -> tuple:
    return (settings,)


@pytest.mark.asyncio
async def test_cloud_project_search_paginates_by_start_at(
    readonly_respx_router: respx.MockRouter, common
) -> None:

    settings = make_settings("cloud")
    client = JiraClient(settings, common=common)
    url = project_search_url(settings.base_url, "cloud")
    readonly_respx_router.get(url).mock(
        return_value=httpx.Response(
            200,
            json={"isLast": False, "total": 2, "values": [{"key": "PAY", "name": "Payments"}]},
        )
    )
    page = await client.list_projects(max_results=1)
    assert page.next_cursor_state == {"startAt": 1}
    await client.aclose()


@pytest.mark.asyncio
async def test_board_sprints_pagination_offset(
    readonly_respx_router: respx.MockRouter, common
) -> None:

    settings = make_settings("server")
    client = JiraClient(settings, common=common)
    readonly_respx_router.get(board_sprints_url(settings.base_url, 7)).mock(
        return_value=httpx.Response(
            200,
            json={"isLast": False, "values": [{"id": 1, "name": "s1", "state": "active"}]},
        )
    )
    page = await client.list_board_sprints(7, state="active", max_results=1)
    assert page.next_cursor_state == {"startAt": 1}
    await client.aclose()
