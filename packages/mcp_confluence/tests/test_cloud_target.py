"""T-113 (CHG-003): point the real `ConfluenceClient` at the live tenant
`https://tnexwm.atlassian.net` through env / `*_FILE`, and confirm the egress guard requires
`*.atlassian.net` on the pull path.

Config only — no secret value is ever committed (the token is a fake read-only placeholder, loaded
through `MCP_CONFLUENCE_API_TOKEN_FILE` to exercise the ADR-0005 `*_FILE` path). The real-tenant
network arm is TC-121 (`@live`, not in `make ci`); here everything is `respx` + a fake token.

Covers: FR-023/AC-001, FR-025/AC-001 (ADR-0023 §6c, ADR-0007), FR-024/AC-001.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings, load_settings
from mcp_common.egress import EgressDenied
from mcp_confluence.client import SOURCE, ConfluenceClient
from mcp_confluence.settings import Settings

# The organisation's real Confluence Cloud tenant (ADR-0023 §6c). Confluence Cloud serves the REST
# API under `/wiki` — the base_url includes it, matching the connector + CHG-003 config.
TENANT = "https://tnexwm.atlassian.net"
BASE = f"{TENANT}/wiki"
# A fake, non-secret read-only token placeholder. NEVER a real credential (E-003 / L-001).
FAKE_TOKEN = "fake-ro-token-not-a-real-secret"


def _write_token_file(tmp_path, value: str = FAKE_TOKEN) -> str:
    path = tmp_path / "confluence.token"
    path.write_text(value + "\n", encoding="utf-8")  # trailing newline: the loader must strip it
    return str(path)


def test_T113_settings_target_the_real_tenant_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`MCP_CONFLUENCE_BASE_URL=https://tnexwm.atlassian.net/wiki` + flavor cloud load cleanly."""
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.setenv("MCP_CONFLUENCE_FLAVOR", "cloud")
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc-readonly@tnex.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN", FAKE_TOKEN)

    settings = load_settings(Settings, source=SOURCE)

    assert settings.base_url == BASE  # trailing slash trimmed by the validator, /wiki kept
    assert settings.flavor == "cloud"
    assert "tnexwm.atlassian.net" in settings.base_url


def test_T113_api_token_loads_from_a_FILE_without_the_plain_env_var(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0005 `*_FILE` path: the token is read from the file the operator points at, so the
    real secret never has to be a committed env literal (CE5 owns .env.example placeholders)."""
    monkeypatch.delenv("MCP_CONFLUENCE_API_TOKEN", raising=False)
    monkeypatch.setenv("MCP_CONFLUENCE_BASE_URL", BASE)
    monkeypatch.setenv("MCP_CONFLUENCE_FLAVOR", "cloud")
    monkeypatch.setenv("MCP_CONFLUENCE_EMAIL", "svc-readonly@tnex.test")
    monkeypatch.setenv("MCP_CONFLUENCE_API_TOKEN_FILE", _write_token_file(tmp_path))

    settings = load_settings(Settings, source=SOURCE)

    assert settings.api_token.get_secret_value() == FAKE_TOKEN  # stripped of the trailing newline
    # The secret is never rendered by repr/str (SecretStr) — guards E-003 at the config boundary.
    assert FAKE_TOKEN not in repr(settings) and FAKE_TOKEN not in str(settings)


@pytest.mark.asyncio
async def test_T113_pull_to_the_tenant_requires_atlassian_on_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `*.atlassian.net` on `MCP_EGRESS_ALLOWLIST` the pull to the real tenant proceeds; the
    guard admits `tnexwm.atlassian.net` for `*.atlassian.net` (connector sets enforce_egress)."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    client = ConfluenceClient(
        Settings(base_url=BASE, email="svc@tnex.test", api_token=FAKE_TOKEN),  # type: ignore[arg-type]
        common=CommonSettings(http_backoff_base=0.0),
        enforce_egress=True,
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url__startswith=f"{BASE}/rest/api/content/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            payload = await client.search("type = page", limit=1, start=0, expand="")
            assert payload == {"results": []}
            assert route.call_count == 1
            assert route.calls.last.request.method == "GET"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_T113_pull_to_the_tenant_is_denied_when_the_allowlist_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-deny: with no allow-list the pull to the real tenant is refused before any socket —
    `*.atlassian.net` is REQUIRED on `MCP_EGRESS_ALLOWLIST` for the Confluence pull path."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "")  # explicit deny-all
    client = ConfluenceClient(
        Settings(base_url=BASE, email="svc@tnex.test", api_token=FAKE_TOKEN),  # type: ignore[arg-type]
        common=CommonSettings(http_backoff_base=0.0),
        enforce_egress=True,
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url__startswith=f"{BASE}/rest/api/content/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            with pytest.raises(EgressDenied) as exc:
                await client.search("type = page", limit=1, start=0, expand="")
            assert route.call_count == 0  # refused before any connection
            assert "tnexwm.atlassian.net" in str(exc.value.host)
            # The denial carries only the host + allow-list, never the token (E-003 / L-001).
            assert FAKE_TOKEN not in str(exc.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_T113_a_lookalike_host_does_not_satisfy_the_atlassian_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`*.atlassian.net` must not admit `tnexwm.atlassian.net.evil.example` (fnmatch anchoring)."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "*.atlassian.net")
    lookalike = "https://tnexwm.atlassian.net.evil.example/wiki"
    client = ConfluenceClient(
        Settings(base_url=lookalike, email="svc@tnex.test", api_token=FAKE_TOKEN),  # type: ignore[arg-type]
        common=CommonSettings(http_backoff_base=0.0),
        enforce_egress=True,
    )
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(url__startswith=lookalike).mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            with pytest.raises(EgressDenied):
                await client.search("type = page", limit=1, start=0, expand="")
    finally:
        await client.aclose()
