"""T-111 · servers keep stdio and reach only their own upstream (ADR-0023 §6e#4, NFR-012/013).

The CHG-003 egress relaxation is a property of the `mcp-ingest` pull + model-download path, NOT
of the 9 MCP servers answering the client over stdio. This regression proves the relaxation did
not leak into the servers:

* a client built the way a *server* builds it (`build_client()` with the default
  `enforce_egress=False`) has **no** egress hook — the server keeps reaching its own upstream read
  API exactly as before CHG-003;
* egress enforcement is opt-in and only the ingest connectors turn it on (asserted structurally);
* no server imports or wires `check_egress` into its serving path (single choke point, L-001).

The live "0 listening sockets over real stdio" arm of TC-125/126 is an E2E QA assertion over the
spawned servers (e2e/test_stdio_readonly_e2e.py); this unit regression guards the code-level
invariant that the servers did not gain egress enforcement.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.http import build_client


@pytest.mark.respx(base_url="https://any-upstream.example")
@pytest.mark.asyncio
async def test_server_client_reaches_its_own_upstream_without_egress_allowlist(
    respx_mock: respx.MockRouter,
) -> None:
    """A server-style client (default enforce_egress=False) reaches its upstream with an EMPTY
    egress allow-list — the servers are not subject to the ingest-pull default-deny."""
    route = respx_mock.get("/health").mock(return_value=httpx.Response(200, json={"ok": True}))

    # Empty allow-list (default) would deny everything IF egress were enforced here.
    async with build_client(settings=CommonSettings()) as client:
        response = await client.get("https://any-upstream.example/health")

    assert response.status_code == 200
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_default_build_client_installs_no_egress_hook() -> None:
    """Structural: the default client has only the read-only transport hook, no egress hook."""
    async with build_client(settings=CommonSettings()) as client:
        request_hooks = client.event_hooks.get("request", [])
    hook_names = [getattr(h, "__name__", repr(h)) for h in request_hooks]
    assert any("readonly" in name for name in hook_names)
    assert not any("egress" in name for name in hook_names)


@pytest.mark.asyncio
async def test_enforce_egress_client_adds_exactly_one_egress_hook() -> None:
    """The opt-in path adds exactly one egress hook on top of the read-only hook."""
    async with build_client(settings=CommonSettings(), enforce_egress=True) as client:
        request_hooks = client.event_hooks.get("request", [])
    hook_names = [getattr(h, "__name__", repr(h)) for h in request_hooks]
    assert sum("egress" in name for name in hook_names) == 1
    assert any("readonly" in name for name in hook_names)


def test_server_packages_do_not_enable_egress_enforcement() -> None:
    """Single choke point (L-001): only the ingest connectors turn egress enforcement on.

    Grep the server packages' source for `enforce_egress=True` — the servers must never set it,
    so egress stays confined to the `mcp-ingest` pull path.
    """
    import pathlib

    packages_root = pathlib.Path(__file__).resolve().parents[3]
    server_packages = [
        "mcp_confluence",
        "mcp_gitlab",
        "mcp_jira",
        "mcp_opensearch",
        "mcp_kibana",
        "mcp_cloudwatch",
        "mcp_kafka",
        "mcp_redis",
        "mcp_sqs_sns",
    ]
    offenders: list[str] = []
    for package in server_packages:
        src = packages_root / package / "src" / package
        for path in src.rglob("*.py"):
            if "enforce_egress=True" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(packages_root)))
    assert offenders == [], f"server package sets enforce_egress=True: {offenders}"
