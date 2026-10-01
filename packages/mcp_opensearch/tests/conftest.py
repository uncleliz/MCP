"""Shared fixtures for mcp-opensearch tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp_common.config import CommonSettings
from mcp_opensearch.client import OpenSearchClient
from mcp_opensearch.read_api import OpenSearchReadApi
from mcp_opensearch.settings import Settings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from os_helpers import (  # noqa: E402
    CAT_INDICES,
    INDEX,
    MAPPING_RESPONSE,
    FakeOpenSearch,
    hit,
    search_response,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        hosts="https://opensearch.example.test:9200",
        username="mcp_ro",
        password=SecretStr("os-not-real-pw"),
        allow_dsl=True,
    )


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def fake() -> FakeOpenSearch:
    return FakeOpenSearch(
        {
            "search": search_response([hit("aBcD1234"), hit("eFgH5678", "2026-09-30T10:40:00Z")]),
            "count": {"count": 1842},
            "cat.indices": CAT_INDICES,
            "indices.get_mapping": MAPPING_RESPONSE,
        }
    )


@pytest.fixture
def client(settings: Settings, common: CommonSettings, fake: FakeOpenSearch) -> OpenSearchClient:
    return OpenSearchClient(settings, common=common, os_client=fake)


@pytest.fixture
def read_api(client: OpenSearchClient, common: CommonSettings) -> OpenSearchReadApi:
    return OpenSearchReadApi(client, common)


__all__ = ["INDEX"]
