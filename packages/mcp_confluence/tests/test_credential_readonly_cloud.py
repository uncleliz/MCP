"""T-114 (CHG-003) / TC-116: the Confluence Cloud read-only gate on the REAL tenant.

`verify_credentials` already proves the write-capable/read-only logic (test_client.py). This file
re-proves it specifically for the **Cloud** flavor at `tnexwm.atlassian.net`, drives the `doctor`
CLI arm end to end (exit code + stdout), and asserts the startup gate refuses to serve a
write-capable account (ADR-0023 §6c/§6e#4, ADR-0003 A1 / ADR-0007 A2, NFR-014).

Fixtures + a fake read-only token only — no live network, no real credential.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings, SourceMisconfiguredError
from mcp_common.runtime import run_credential_check
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.settings import Settings

from mcp_confluence import cli

TENANT = "https://tnexwm.atlassian.net"
BASE = f"{TENANT}/wiki"
FAKE_TOKEN = "fake-ro-token-not-a-real-secret"
FIXTURES = Path(__file__).parent / "fixtures" / "confluence"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _mock_identity(router: respx.MockRouter, sample: str) -> None:
    router.get(f"{BASE}/rest/api/user/current").mock(
        return_value=httpx.Response(200, json=_fixture("current_user.json"))
    )
    router.get(f"{BASE}/rest/api/content/search").mock(
        return_value=httpx.Response(200, json=_fixture(sample))
    )


def _client() -> ConfluenceClient:
    return ConfluenceClient(
        Settings(base_url=BASE, email="svc-readonly@tnex.test", api_token=FAKE_TOKEN),  # type: ignore[arg-type]
        common=CommonSettings(http_backoff_base=0.0),
    )


@pytest.mark.asyncio
async def test_TC116_cloud_readonly_account_passes_the_gate(
    readonly_respx_router: respx.MockRouter,
) -> None:
    client = _client()
    try:
        _mock_identity(readonly_respx_router, "sample_page_readonly.json")
        report = await client.verify_credentials()
        assert report.ok, report.reasons
        assert await client.credential_check() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_TC116_cloud_write_capable_account_is_refused_naming_the_write_op(
    readonly_respx_router: respx.MockRouter,
) -> None:
    client = _client()
    try:
        _mock_identity(readonly_respx_router, "sample_page_writable.json")
        report = await client.verify_credentials()
        assert not report.ok
        # The refusal names the permitted write operation(s) it saw (TC-116 expected).
        joined = " ".join(report.reasons)
        assert "not read-only" in joined
        assert any(verb in joined for verb in ("create", "update", "delete"))
        assert await client.credential_check() is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_TC116_startup_gate_refuses_to_serve_a_write_capable_cloud_account(
    readonly_respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve`'s credential gate (ADR-0003 A1) refuses the write-capable account: no bypass except
    the explicit `MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` escape hatch."""
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    client = _client()
    try:
        _mock_identity(readonly_respx_router, "sample_page_writable.json")
        with pytest.raises(SourceMisconfiguredError):
            await run_credential_check(
                client.credential_check, settings=CommonSettings(), server_name="confluence"
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_TC116_escape_hatch_logs_a_warning_and_serves_anyway(
    readonly_respx_router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """`MCP_ALLOW_UNVERIFIED_CREDENTIALS=true` logs one WARN per start and proceeds; it must not be
    used for the real token (ADR-0023 §6c). Here we assert the WARN is emitted."""
    import logging

    monkeypatch.setenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", "true")
    client = _client()
    try:
        _mock_identity(readonly_respx_router, "sample_page_writable.json")
        with caplog.at_level(logging.WARNING):
            await run_credential_check(
                client.credential_check, settings=CommonSettings(), server_name="confluence"
            )
        assert any(record.levelno == logging.WARNING for record in caplog.records)
    finally:
        await client.aclose()


def test_TC116_doctor_cli_reports_readonly_ok_at_the_real_tenant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The `mcp-confluence doctor` CLI arm, driven end to end against the tenant fixture: a
    read-only account exits 0 and prints the base_url + an ok line; the token never prints."""
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.setenv("MCP_CONFLUENCE_FLAVOR", "cloud")
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc-readonly@tnex.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN", FAKE_TOKEN)
    with respx.mock(assert_all_called=False) as router:
        _mock_identity(router, "sample_page_readonly.json")
        code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert "tnexwm.atlassian.net" in out and "flavor=cloud" in out
    assert "ok" in out.lower()
    assert FAKE_TOKEN not in out  # the token value never reaches stdout


def test_TC116_doctor_cli_refuses_a_write_capable_account_at_the_real_tenant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A write-capable account makes `doctor` exit non-zero and name the FAILED read-only check."""
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.setenv("MCP_CONFLUENCE_FLAVOR", "cloud")
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc@tnex.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN", FAKE_TOKEN)
    with respx.mock(assert_all_called=False) as router:
        _mock_identity(router, "sample_page_writable.json")
        code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAILED" in out
    assert any(verb in out for verb in ("create", "update", "delete"))
    assert FAKE_TOKEN not in out
