"""T-038: endpoint allowlist, headers, startup check of mcp_kibana.client."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from kibana_helpers import API, BASE, DASH_ID, NOT_FOUND_BODY, STATUS_OK, find_url, get_url, ok
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_kibana.client import ALLOWED_OPERATIONS, OP_FIND, OP_GET, OP_STATUS, KibanaClient


def test_FR_005_allowlist_is_exactly_three_get_endpoints() -> None:
    assert set(ALLOWED_OPERATIONS) == {
        "GET /api/saved_objects/_find",
        "GET /api/saved_objects/{type}/{id}",
        "GET /api/status",
    }
    assert all(op.startswith("GET ") for op in ALLOWED_OPERATIONS)


@pytest.mark.parametrize(
    "operation",
    [
        "POST /api/saved_objects/_find",
        "GET /api/saved_objects/_export",
        "GET /api/security/role",
        "DELETE /api/saved_objects/{type}/{id}",
        "GET /api/spaces/space",
    ],
)
@pytest.mark.asyncio
async def test_operations_outside_the_allowlist_are_not_permitted(
    client: KibanaClient, kibana: respx.MockRouter, operation: str
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get(operation, {"type": "dashboard", "id": "x"})
    assert exc.value.details["operation"] == operation and kibana.calls.call_count == 0


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
@pytest.mark.asyncio
async def test_write_methods_are_blocked_by_the_shared_transport(
    client: KibanaClient, kibana: respx.MockRouter, method: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{API}/saved_objects/dashboard/x")
    assert kibana.calls.call_count == 0


@pytest.mark.asyncio
async def test_get_sends_basic_auth_and_never_kbn_xsrf(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    kibana.get(find_url()).mock(return_value=ok({"saved_objects": []}))
    await client.get(OP_FIND, params={"search": "x*", "type": ["dashboard", "lens"], "page": 1})
    request = kibana.calls.last.request
    token = base64.b64encode(b"mcp_ro:kibana-not-real-pw").decode()
    assert request.headers["authorization"] == f"Basic {token}"
    assert "kbn-xsrf" not in request.headers  # only write methods need it; we never write
    assert request.url.params.get_list("type") == ["dashboard", "lens"]


@pytest.mark.asyncio
async def test_space_prefixes_the_path_and_ids_are_quoted(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    kibana.get(f"{BASE}/s/ops/api/saved_objects/dashboard/a%2Fb").mock(return_value=ok({"id": 1}))
    assert (await client.get(OP_GET, {"type": "dashboard", "id": "a/b"}, space="ops")) == {"id": 1}


@pytest.mark.asyncio
async def test_invalid_space_id_is_rejected_without_a_call(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    with pytest.raises(ToolError) as exc:
        await client.get(OP_FIND, space="../admin")
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == "space"
    assert kibana.calls.call_count == 0


@pytest.mark.asyncio
async def test_non_json_response_is_upstream_error(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    kibana.get(f"{API}/status").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    with pytest.raises(ToolError) as exc:
        await client.get(OP_STATUS)
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR and "MCP_KIBANA_BASE_URL" in exc.value.message


@pytest.mark.asyncio
async def test_404_maps_to_upstream_error_with_status_for_not_found_detection(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    kibana.get(get_url("dashboard", DASH_ID)).mock(
        return_value=httpx.Response(404, json=NOT_FOUND_BODY)
    )
    with pytest.raises(ToolError) as exc:
        await client.get(OP_GET, {"type": "dashboard", "id": DASH_ID})
    assert exc.value.details["upstream_status"] == 404


# -- startup check --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_check_green(client: KibanaClient, kibana: respx.MockRouter) -> None:
    kibana.get(f"{API}/status").mock(return_value=ok(STATUS_OK))
    report = await client.verify_credentials()
    assert report.ok and report.reasons == []
    assert await client.credential_check() is True


@pytest.mark.parametrize(
    "payload",
    [
        {"status": {"overall": {"level": "unavailable"}}},
        {"status": {"overall": {"level": "critical"}}},
        {"status": {"overall": {"state": "red"}}},
        {"status": {}},
        ["not", "a", "dict"],
    ],
)
@pytest.mark.asyncio
async def test_startup_check_red_when_kibana_is_not_available(
    client: KibanaClient, kibana: respx.MockRouter, payload
) -> None:
    kibana.get(f"{API}/status").mock(return_value=ok(payload))
    report = await client.verify_credentials()
    assert not report.ok and report.reasons


@pytest.mark.parametrize(("status", "code"), [(401, "unauthorized"), (403, "forbidden")])
@pytest.mark.asyncio
async def test_startup_check_red_on_auth_failure_with_reason(
    client: KibanaClient, kibana: respx.MockRouter, status: int, code: str
) -> None:
    kibana.get(f"{API}/status").mock(return_value=httpx.Response(status, json={}))
    report = await client.verify_credentials()
    assert not report.ok and code in report.reasons[0]
    assert await client.credential_check() is False


@pytest.mark.asyncio
async def test_startup_check_red_when_unreachable(
    client: KibanaClient, kibana: respx.MockRouter
) -> None:
    kibana.get(f"{API}/status").mock(side_effect=httpx.ConnectError("refused"))
    report = await client.verify_credentials()
    assert not report.ok and "upstream_unavailable" in report.reasons[0]


def test_source_never_sends_kbn_xsrf_or_write_verbs() -> None:
    import re
    from pathlib import Path

    import mcp_kibana

    pattern = re.compile(
        r"""['"]kbn-xsrf['"]|\.(post|put|delete|patch)\(|['"](POST|PUT|DELETE|PATCH)['"]"""
    )
    offenders = [
        f"{p.name}:{n}"
        for p in Path(mcp_kibana.__file__).parent.glob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
