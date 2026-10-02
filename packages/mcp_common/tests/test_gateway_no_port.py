"""T-109 (E7, ADR-0021) — the gateway opens NO network port; audit is stderr-only.

FR-022/AC-003 + NFR-012 (EB-005/BR-012 regression): the in-process gateway-boundary must
open zero listening sockets (stdio only) and all audit/logs go to stderr, never stdout.

Two independent defences are asserted:

1. **Behavioural** — while the gateway is constructed and a call is dispatched, no socket
   is ever bound or put into the listening state. We instrument ``socket.socket`` so any
   ``bind()``/``listen()`` is recorded; the assertion is that none happened. This is
   portable (no psutil / no ``/proc``) and deterministic.
2. **Structural** — the gateway module's own source imports no network-server machinery
   (``socket``, ``http.server``, ``socketserver``, ``uvicorn``, ``asyncio`` servers), so
   it *cannot* open a port by construction. A drift here fails the test.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import mcp_common.gateway as gateway_module
import pytest
from mcp_common.gateway import AuthContext, Gateway


class _SocketSentinel:
    """Records every real socket ``bind``/``listen`` for the duration of a test."""

    def __init__(self) -> None:
        self.binds: list[object] = []
        self.listens: list[object] = []


@pytest.fixture
def socket_sentinel(monkeypatch: pytest.MonkeyPatch) -> _SocketSentinel:
    sentinel = _SocketSentinel()
    real_bind = socket.socket.bind
    real_listen = socket.socket.listen

    def spy_bind(self, address):  # type: ignore[no-untyped-def]
        sentinel.binds.append(address)
        return real_bind(self, address)

    def spy_listen(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        sentinel.listens.append(args)
        return real_listen(self, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "bind", spy_bind)
    monkeypatch.setattr(socket.socket, "listen", spy_listen)
    return sentinel


@pytest.mark.asyncio
async def test_AC_003_gateway_opens_no_listening_socket_through_full_lifecycle(
    socket_sentinel: _SocketSentinel,
) -> None:
    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return {"ok": True}

    # Construct, register, dispatch — the whole in-process lifecycle.
    gw = Gateway(rate_limit_capacity=10, rate_limit_refill_per_s=10)
    gw.register("mcp-knowledge", "search_company_knowledge", handler)
    result = await gw.dispatch("search_company_knowledge", {"q": "x"}, AuthContext())

    assert result == {"ok": True}
    # Zero sockets bound, zero listening sockets opened (stdio only, NFR-005/NFR-012).
    assert socket_sentinel.binds == []
    assert socket_sentinel.listens == []


def test_AC_003_gateway_module_imports_no_network_server_machinery() -> None:
    """Structural: the gateway cannot open a port because it imports nothing that could.

    EB-005/BR-012 regression — a future edit that pulls in a socket/HTTP server here
    would break NFR-005; this fails the moment such an import appears.
    """
    source = Path(gateway_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    banned = {"socket", "socketserver", "http.server", "uvicorn", "starlette", "fastapi"}
    imported_at_module_level: set[str] = set()
    for node in tree.body:  # module-level imports only
        if isinstance(node, ast.Import):
            imported_at_module_level.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_at_module_level.add(node.module)

    leaked = {name for name in imported_at_module_level if name in banned}
    assert leaked == set(), f"gateway must not import network-server machinery: {leaked}"


def test_AC_003_default_transport_declares_no_network_port() -> None:
    gw = Gateway(rate_limit_capacity=10, rate_limit_refill_per_s=10)
    assert gw.transport.opens_network_port() is False


@pytest.mark.asyncio
async def test_AC_003_all_audit_goes_to_stderr_never_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return {"ok": True}

    gw = Gateway(rate_limit_capacity=10, rate_limit_refill_per_s=10)
    gw.register("mcp-knowledge", "get_service", handler)
    await gw.dispatch("get_service", {"name": "payments"})

    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout — stdout is the JSON-RPC channel
    assert captured.err.strip() != ""  # the audit record is on stderr


def test_AC_003_a_transport_that_opens_a_port_is_refused() -> None:
    class RoguePortTransport:
        name = "rogue"

        def opens_network_port(self) -> bool:
            return True

    with pytest.raises(ValueError, match="network port"):
        Gateway(transport=RoguePortTransport())  # type: ignore[arg-type]
