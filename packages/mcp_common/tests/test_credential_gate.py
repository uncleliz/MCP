"""T-012: mcp_common.runtime — startup credential gate (ADR-0003 A1) + serve()
fail-closed transport check.
"""

from __future__ import annotations

import pytest
from mcp_common.config import CommonSettings, SourceMisconfiguredError
from mcp_common.logging import setup_logging
from mcp_common.runtime import run_credential_check, serve


@pytest.mark.asyncio
async def test_passing_check_does_not_raise() -> None:
    settings = CommonSettings()
    await run_credential_check(lambda: True, settings=settings, server_name="confluence")


@pytest.mark.asyncio
async def test_async_passing_check_does_not_raise() -> None:
    async def check() -> bool:
        return True

    settings = CommonSettings()
    await run_credential_check(check, settings=settings, server_name="confluence")


@pytest.mark.asyncio
async def test_failing_check_raises_source_misconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    settings = CommonSettings()

    with pytest.raises(SourceMisconfiguredError):
        await run_credential_check(lambda: False, settings=settings, server_name="pgvector")


@pytest.mark.asyncio
async def test_check_raising_exception_is_treated_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    settings = CommonSettings()

    def check() -> bool:
        raise RuntimeError("network unreachable")

    with pytest.raises(SourceMisconfiguredError):
        await run_credential_check(check, settings=settings, server_name="pgvector")


@pytest.mark.asyncio
async def test_escape_hatch_allows_serving_despite_failed_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", "true")
    settings = CommonSettings()
    logger = setup_logging("pgvector")

    await run_credential_check(
        lambda: False, settings=settings, server_name="pgvector", logger=logger
    )

    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "pgvector" in captured.err


@pytest.mark.asyncio
async def test_serve_fails_closed_on_non_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    build_calls = []

    def build_server_fn():
        build_calls.append(1)
        raise AssertionError("should never be called")  # pragma: no cover

    with pytest.raises(SourceMisconfiguredError):
        await serve(build_server_fn, server_name="confluence")

    assert build_calls == []


@pytest.mark.asyncio
async def test_serve_does_not_build_server_when_credential_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    build_calls = []

    def build_server_fn():
        build_calls.append(1)
        raise AssertionError("should never be called")  # pragma: no cover

    with pytest.raises(SourceMisconfiguredError):
        await serve(
            build_server_fn,
            server_name="pgvector",
            credential_check=lambda: False,
        )

    assert build_calls == []


@pytest.mark.asyncio
async def test_serve_happy_path_builds_and_runs_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)

    class _FakeServer:
        def __init__(self) -> None:
            self.ran = False

        async def run_stdio_async(self) -> None:
            self.ran = True

    fake_server = _FakeServer()

    await serve(
        lambda: fake_server,
        server_name="confluence",
        credential_check=lambda: True,
    )

    assert fake_server.ran is True
