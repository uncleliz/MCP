"""Shared fixtures for mcp-confluence tests.

The JSON fixtures under tests/fixtures/confluence/ are hand-written to the shape of the
Confluence Cloud REST API (`/wiki/rest/api/...`) as documented by Atlassian — this
container has no live Confluence, so they have NOT been captured from a real tenant.
`test_integration.py` (marker `live`) is the check against the real thing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp_common.config import CommonSettings
from mcp_common.testing import readonly_respx_router  # noqa: F401  (re-exported fixture)
from mcp_confluence.client import ConfluenceClient
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.settings import Settings
from pydantic import SecretStr

FIXTURES = Path(__file__).parent / "fixtures" / "confluence"
BASE = "https://acme.atlassian.net/wiki"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture() -> Callable[[str], Any]:
    return load_fixture


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url=BASE, email="svc-readonly@acme.test", api_token=SecretStr("tok-not-a-real-secret")
    )


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings(http_backoff_base=0.0)


@pytest.fixture
def client(settings: Settings, common: CommonSettings) -> ConfluenceClient:
    return ConfluenceClient(settings, common=common)


@pytest.fixture
def read_api(
    client: ConfluenceClient, settings: Settings, common: CommonSettings
) -> ConfluenceReadApi:
    return ConfluenceReadApi(client, settings, common)
