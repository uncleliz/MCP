"""Shared fixtures for mcp-kibana tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.testing import readonly_respx_router  # noqa: F401  (re-exported fixture)
from mcp_kibana.client import KibanaClient
from mcp_kibana.read_api import KibanaReadApi
from mcp_kibana.settings import Settings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from kibana_helpers import BASE  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url=BASE, username="mcp_ro", password=SecretStr("kibana-not-real-pw"))


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings(http_backoff_base=0.0)


@pytest.fixture
def client(settings: Settings, common: CommonSettings) -> KibanaClient:
    return KibanaClient(settings, common=common)


@pytest.fixture
def read_api(client: KibanaClient, common: CommonSettings) -> KibanaReadApi:
    return KibanaReadApi(client, common)


@pytest.fixture
def kibana(readonly_respx_router: respx.MockRouter) -> respx.MockRouter:  # noqa: F811
    return readonly_respx_router
