"""Shared fixtures for mcp-jira tests.

The JSON fixtures under tests/fixtures/jira/ are hand-written to the shape of the Jira REST API
(Cloud `/rest/api/3` + Server/DC `/rest/api/2` + Agile `/rest/agile/1.0`) as documented by
Atlassian — this container has no live Jira, so they have NOT been captured from a real tenant
(ADR-0019 Risks; spike S1). `test_integration.py` (marker `live`) is the check against the real
thing, run once per flavor.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp_common.config import CommonSettings
from mcp_common.testing import readonly_respx_router  # noqa: F401  (re-exported fixture)
from mcp_jira.client import JiraClient
from mcp_jira.read_api import JiraReadApi
from mcp_jira.settings import Settings
from pydantic import SecretStr

FIXTURES = Path(__file__).parent / "fixtures" / "jira"
CLOUD_BASE = "https://acme.atlassian.net"
SERVER_BASE = "https://jira.acme.example"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture() -> Callable[[str], Any]:
    return load_fixture


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings(http_backoff_base=0.0)


def make_settings(flavor: str) -> Settings:
    if flavor == "cloud":
        return Settings(
            base_url=CLOUD_BASE,
            flavor="cloud",
            email="svc-readonly@acme.test",
            token=SecretStr("tok-not-a-real-secret"),
        )
    return Settings(
        base_url=SERVER_BASE,
        flavor="server",
        token=SecretStr("pat-not-a-real-secret"),
    )


@pytest.fixture(params=["cloud", "server"])
def flavor(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def settings(flavor: str) -> Settings:
    return make_settings(flavor)


@pytest.fixture
def base(settings: Settings) -> str:
    return settings.base_url


@pytest.fixture
def client(settings: Settings, common: CommonSettings) -> JiraClient:
    return JiraClient(settings, common=common)


@pytest.fixture
def read_api(client: JiraClient, settings: Settings, common: CommonSettings) -> JiraReadApi:
    return JiraReadApi(client, settings, common)


# Cloud-only settings/client when a test is flavor-specific.
@pytest.fixture
def cloud_settings() -> Settings:
    return make_settings("cloud")


@pytest.fixture
def server_settings() -> Settings:
    return make_settings("server")
