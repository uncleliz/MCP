"""T-112 · OpenSearch ingest-pull egress is routed through the one egress guard.

The OpenSearch connector reuses `OpenSearchClient` over `opensearch-py`, whose I/O seam is
`ReadOnlyTransport.perform_request`. When the connector builds the client with
`enforce_egress=True`, that seam must also run `mcp_common.egress.check_egress` — an unlisted
OpenSearch host is refused default-deny before any request; an allow-listed one proceeds.
"""

from __future__ import annotations

import types

import pytest
from mcp_common.egress import EgressDenied
from mcp_opensearch.client import OpenSearchClient, ReadOnlyTransport
from mcp_opensearch.settings import Settings
from pydantic import SecretStr


def _settings(host: str) -> Settings:
    return Settings(
        hosts=f"https://{host}:9200",
        username="mcp_ro",
        password=SecretStr("fake-not-real"),
    )


def test_build_sdk_client_uses_egress_guarded_transport_when_enforced() -> None:
    """enforce_egress=True picks a transport subclass that enforces egress; False does not."""
    guarded = OpenSearchClient._build_sdk_client(
        _settings("os.internal.example"), enforce_egress=True
    )
    plain = OpenSearchClient._build_sdk_client(_settings("os.internal.example"))
    assert guarded.transport._enforce_egress is True
    assert plain.transport._enforce_egress is False


def _transport_with_host(host: str, *, enforce: bool) -> ReadOnlyTransport:
    """A ReadOnlyTransport subclass instance whose connection pool reports `host`, without I/O."""
    cls = type(
        "T", (ReadOnlyTransport,), {"_enforce_egress": enforce}
    )
    transport = cls.__new__(cls)  # skip opensearch-py's network __init__
    connection = types.SimpleNamespace(host=f"https://{host}:9200")
    transport.connection_pool = types.SimpleNamespace(connections=[connection])
    return transport


def test_egress_target_reads_host_from_connection_pool() -> None:
    transport = _transport_with_host("os.internal.example", enforce=False)
    assert "os.internal.example" in transport._egress_target()


@pytest.mark.asyncio
async def test_perform_request_refuses_unlisted_opensearch_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    transport = _transport_with_host("os.internal.example", enforce=True)
    with pytest.raises(EgressDenied):
        await transport.perform_request("GET", "/my-index/_search")


@pytest.mark.asyncio
async def test_perform_request_allows_listed_opensearch_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "os.internal.example")
    transport = _transport_with_host("os.internal.example", enforce=True)
    called: dict[str, object] = {}

    async def _fake_super(self: object, method: str, url: str, *a: object, **k: object) -> str:
        called["url"] = url
        return "ok"

    monkeypatch.setattr(ReadOnlyTransport.__bases__[0], "perform_request", _fake_super)
    result = await transport.perform_request("GET", "/my-index/_search")
    assert result == "ok"
    assert called["url"] == "/my-index/_search"
