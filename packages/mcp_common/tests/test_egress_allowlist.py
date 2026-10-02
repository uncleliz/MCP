"""T-111 · egress allow-list honoured (ADR-0023 §6e#2, L-001) — the configured source host
is permitted; an unconfigured host fails closed before any connection is attempted.

Covers TC-111 (positive: an allow-listed host is permitted and the pull proceeds through the
guard) and TC-114 (allow-list honoured both ways: `*.atlassian.net` + `huggingface.co` permitted,
an unconfigured host fails closed, no connection attempted).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied, check_egress, host_allowed
from mcp_common.http import build_client

ALLOWLIST = ["*.atlassian.net", "huggingface.co"]


def test_configured_atlassian_host_is_permitted() -> None:
    """TC-111 positive / §6e#2: the configured Confluence Cloud host is permitted."""
    assert check_egress("tnexwm.atlassian.net", allowlist=ALLOWLIST) == "tnexwm.atlassian.net"


def test_huggingface_host_is_permitted_on_the_model_path() -> None:
    """FR-027/AC-002: `huggingface.co` is permitted (model-download path)."""
    assert check_egress("https://huggingface.co/BAAI/bge-m3", allowlist=ALLOWLIST) == (
        "huggingface.co"
    )


def test_unconfigured_host_fails_closed() -> None:
    """TC-114 negative: a host outside the allow-list fails closed with a clear error."""
    with pytest.raises(EgressDenied) as exc_info:
        check_egress("gitlab.internal.example", allowlist=ALLOWLIST)
    assert "gitlab.internal.example" in str(exc_info.value)


def test_host_allowed_matrix() -> None:
    assert host_allowed("tnexwm.atlassian.net", ALLOWLIST) is True
    assert host_allowed("huggingface.co", ALLOWLIST) is True
    assert host_allowed("huggingface.co.evil", ALLOWLIST) is False
    assert host_allowed("", ALLOWLIST) is False


@pytest.mark.respx(base_url="https://tnexwm.atlassian.net")
@pytest.mark.asyncio
async def test_allowlisted_host_proceeds_through_guarded_client(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-111: a GET to an allow-listed host passes the egress hook and reaches the route."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net,huggingface.co")
    route = respx_mock.get("/wiki/rest/api/content").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    async with build_client(settings=CommonSettings(), enforce_egress=True) as client:
        response = await client.get("https://tnexwm.atlassian.net/wiki/rest/api/content")

    assert response.status_code == 200
    assert route.call_count == 1


@pytest.mark.respx(assert_all_called=False)
@pytest.mark.asyncio
async def test_unconfigured_host_attempts_no_connection_through_guarded_client(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-114: a connector configured to an unconfigured host attempts no connection."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    route = respx_mock.get("https://gitlab.internal.example/api/v4/projects").mock(
        return_value=httpx.Response(200)
    )

    async with build_client(settings=CommonSettings(), enforce_egress=True) as client:
        with pytest.raises(EgressDenied):
            await client.get("https://gitlab.internal.example/api/v4/projects")

    assert route.call_count == 0
