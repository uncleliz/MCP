"""T-111 · egress default-deny (ADR-0023 §6e#1, L-001) — the adversarial test proving a
host not on the allow-list is refused (not silently allowed) and **no socket is opened**.

Covers TC-113 (default-deny, no socket) plus the raw-guard unit assertions behind it. The
"no socket" guarantee is proven two ways: the pure guard raises before any I/O, and a real
`build_client(enforce_egress=True)` request to an unlisted host is rejected by the event hook
before respx's transport (or any real socket) is ever reached.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied, check_egress, parse_allowlist, resolve_host
from mcp_common.errors import ErrorCode
from mcp_common.http import build_client


def test_empty_allowlist_denies_every_host() -> None:
    """AC-001 default-deny: an empty `MCP_EGRESS_ALLOWLIST` refuses all egress."""
    for host in ("tnexwm.atlassian.net", "huggingface.co", "evil.example.com", "localhost"):
        with pytest.raises(EgressDenied) as exc_info:
            check_egress(host, allowlist=[])
        assert exc_info.value.code == ErrorCode.NOT_PERMITTED
        assert exc_info.value.retryable is False
        assert host in str(exc_info.value)


def test_unlisted_host_refused_even_when_allowlist_has_other_hosts() -> None:
    """ADR-0023 §6e#1 adversarial: an unlisted host is refused, naming the host."""
    with pytest.raises(EgressDenied) as exc_info:
        check_egress("evil.example.com", allowlist=["*.atlassian.net", "huggingface.co"])
    assert exc_info.value.host == "evil.example.com"
    assert "evil.example.com" in str(exc_info.value)


def test_lookalike_host_is_not_a_match() -> None:
    """`*.atlassian.net` must not admit a suffix look-alike (fail-closed anchoring)."""
    for lookalike in (
        "atlassian.net.evil.example",
        "tnexwm.atlassian.net.evil.example",
        "notatlassian.net",
    ):
        with pytest.raises(EgressDenied):
            check_egress(lookalike, allowlist=["*.atlassian.net"])


def test_denied_host_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must refuse *before* any network I/O — assert nothing dials a socket."""
    import socket as socket_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a socket was opened for a denied egress host")

    monkeypatch.setattr(socket_module.socket, "connect", _boom)
    monkeypatch.setattr(socket_module, "create_connection", _boom)

    with pytest.raises(EgressDenied):
        check_egress("evil.example.com", allowlist=["*.atlassian.net"])


@pytest.mark.respx(base_url="https://evil.example.com", assert_all_called=False)
@pytest.mark.asyncio
async def test_build_client_refuses_unlisted_host_before_transport(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: `build_client(enforce_egress=True)` rejects an unlisted host at the hook,
    so the mocked route is never called (no socket to the unlisted host)."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    route = respx_mock.get("/anything").mock(return_value=httpx.Response(200))

    async with build_client(settings=CommonSettings(), enforce_egress=True) as client:
        with pytest.raises(EgressDenied):
            await client.get("https://evil.example.com/anything")

    assert route.call_count == 0


def test_parse_allowlist_handles_blanks_and_case() -> None:
    assert parse_allowlist("") == []
    assert parse_allowlist(None) == []
    assert parse_allowlist(" *.Atlassian.net , huggingface.co ,") == [
        "*.atlassian.net",
        "huggingface.co",
    ]


def test_resolve_host_from_url_and_bare_host() -> None:
    assert resolve_host("https://tnexwm.atlassian.net/wiki/rest/api") == "tnexwm.atlassian.net"
    assert resolve_host("tnexwm.atlassian.net") == "tnexwm.atlassian.net"
    assert resolve_host("https://user:pass@host.example:8443/path") == "host.example"
    assert resolve_host("https://[::1]:9200/_search") == "[::1]"


def test_check_egress_reads_allowlist_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit allowlist arg, check_egress reads MCP_EGRESS_ALLOWLIST via settings."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    assert check_egress("tnexwm.atlassian.net", settings=CommonSettings()) == "tnexwm.atlassian.net"
    with pytest.raises(EgressDenied):
        check_egress("evil.example.com", settings=CommonSettings())


def test_check_egress_empty_settings_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_EGRESS_ALLOWLIST", raising=False)
    with pytest.raises(EgressDenied):
        check_egress("tnexwm.atlassian.net", settings=CommonSettings())
