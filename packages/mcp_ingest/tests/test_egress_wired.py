"""T-112 · the ingest-pull path is wired through the one egress guard (ADR-0023 §6a, L-001).

Proves the connectors reach the network **only** through `mcp_common.egress.check_egress`:

* a connector client built the way `connectors/*.build()` builds it (`enforce_egress=True`) refuses
  a pull to an unlisted host before any connection — the default-deny guard governs the pull
  (TC-113/TC-114 at the connector level);
* a pull to an allow-listed host proceeds (TC-111 at the connector level);
* **structural / no-bypass (TC-112):** every one of the 4 ingestable connectors' `build()` passes
  `enforce_egress=True`, so there is a single choke point and no connector bypasses the guard.

Uses `respx` + a fake token (no real network, no real credential).
"""

from __future__ import annotations

import inspect

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.settings import Settings as ConfluenceSettings
from mcp_ingest.connectors import confluence, gitlab, jira, opensearch
from pydantic import SecretStr

ALLOWED_BASE = "https://tnexwm.atlassian.net/wiki"
UNLISTED_BASE = "https://evil.example.com/wiki"


def _client(base_url: str) -> ConfluenceClient:
    return ConfluenceClient(
        ConfluenceSettings(
            base_url=base_url, email="svc@acme.test", api_token=SecretStr("fake-not-real")
        ),
        common=CommonSettings(http_backoff_base=0.0),
        enforce_egress=True,
    )


@pytest.mark.asyncio
async def test_connector_client_refuses_pull_to_unlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-113/TC-114 at the connector seam: a pull toward an unlisted host is refused."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    client = _client(UNLISTED_BASE)
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url__startswith=UNLISTED_BASE).mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            with pytest.raises(EgressDenied):
                await client.get(
                    "GET /rest/api/content/search", params={"cql": "type=page"}
                )
            assert route.call_count == 0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_connector_client_allows_pull_to_allowlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-111 at the connector seam: a pull to the configured host proceeds through the guard."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    client = _client(ALLOWED_BASE)
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url__startswith=f"{ALLOWED_BASE}/rest/api/content/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            payload = await client.get(
                "GET /rest/api/content/search", params={"cql": "type=page"}
            )
            assert payload == {"results": []}
            assert route.call_count == 1
    finally:
        await client.aclose()


def test_every_ingestable_connector_build_enables_egress_enforcement() -> None:
    """TC-112 structural / no-bypass: all 4 connector `build()` pass `enforce_egress=True`."""
    offenders: list[str] = []
    for module in (confluence, gitlab, jira, opensearch):
        source = inspect.getsource(module.build)
        if "enforce_egress=True" not in source:
            offenders.append(module.__name__)
    assert offenders == [], f"connector build() does not enable egress enforcement: {offenders}"
