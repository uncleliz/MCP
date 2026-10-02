"""T-123 — live egress is gated OFF in CI: `make ci` touches no real credentials and makes no real
outbound call. The real live run (real token, real huggingface.co download) is an operator step
behind ``MCP_INGEST_ALLOW_LIVE_EGRESS=true`` + ``MCP_LIVE_TESTS`` — NEVER part of ``make ci``
(ADR-0023 §7, FR-023/AC-001 operational, NFR-014).

Proven here without any network:

* ``MCP_INGEST_ALLOW_LIVE_EGRESS`` is unset / not ``"true"`` in a plain CI run, so the live arms
  (TC-121 live Confluence, TC-131 real bge-m3 download) **skip**;
* with the default-deny allow-list (empty), the egress guard refuses every host — so even a stray
  outbound attempt in CI is fail-closed, not a silent real call.
"""

from __future__ import annotations

import os

import pytest
from mcp_common.config import CommonSettings
from mcp_common.egress import EgressDenied, check_egress


def test_live_egress_flag_is_off_by_default_in_ci() -> None:
    """The live-egress gate defaults to off: `make ci` never flips it on."""
    assert os.environ.get("MCP_INGEST_ALLOW_LIVE_EGRESS", "false") != "true", (
        "MCP_INGEST_ALLOW_LIVE_EGRESS must not be 'true' in make ci — the live run is a "
        "gated operator step, never part of CI"
    )


def test_default_deny_allowlist_refuses_every_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the allow-list empty (CI default), every outbound host is refused (fail-closed)."""
    monkeypatch.delenv("MCP_EGRESS_ALLOWLIST", raising=False)
    for host in ("tnexwm.atlassian.net", "huggingface.co", "example.com"):
        with pytest.raises(EgressDenied):
            check_egress(host, settings=CommonSettings())


def test_ci_default_denies_both_atlassian_and_huggingface(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh install (no allow-list) reaches neither Atlassian nor HuggingFace — opt-in only."""
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", "")  # explicit empty = default-deny
    with pytest.raises(EgressDenied):
        check_egress("tnexwm.atlassian.net", settings=CommonSettings())
    with pytest.raises(EgressDenied):
        check_egress("huggingface.co", settings=CommonSettings())
