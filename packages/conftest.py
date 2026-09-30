"""Workspace-wide pytest hooks.

`@pytest.mark.live` tests talk to a real upstream (Confluence Cloud, GitLab, ...) and need
credentials + VPN, neither of which exist in CI or the dev container. They are skipped
unless `MCP_LIVE_TESTS=1` is set, with an explicit reason (never a failure).
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("MCP_LIVE_TESTS") == "1":
        return
    skip_live = pytest.mark.skip(
        reason="live integration test: needs real credentials/VPN (set MCP_LIVE_TESTS=1 to run)"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
