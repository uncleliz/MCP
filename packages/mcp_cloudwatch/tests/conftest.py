"""Shared fixtures for mcp-cloudwatch tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp_cloudwatch.client import CloudWatchClient
from mcp_cloudwatch.read_api import CloudWatchReadApi
from mcp_cloudwatch.settings import Settings
from mcp_common.config import CommonSettings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from cw_helpers import REGION, Stubs  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
        region=REGION,
        aws_access_key_id=SecretStr("AKIDEXAMPLEEXAMPLEXX"),
        aws_secret_access_key=SecretStr("not-a-real-secret"),
        insights_poll_interval=0.0,
    )


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def stubs() -> Stubs:
    return Stubs()


@pytest.fixture
def client(settings: Settings, common: CommonSettings, stubs: Stubs) -> CloudWatchClient:
    return CloudWatchClient(settings, common=common, client_factory=stubs.factory())


@pytest.fixture
def read_api(client: CloudWatchClient, common: CommonSettings, settings: Settings):
    return CloudWatchReadApi(client, common, settings)
