"""Shared fixtures for mcp-sqs-sns tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp_common.config import CommonSettings
from mcp_sqs_sns.client import SqsSnsClient
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.settings import Settings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from sqs_helpers import REGION, Stubs  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
        region=REGION,
        aws_access_key_id=SecretStr("AKIDEXAMPLEEXAMPLEXX"),
        aws_secret_access_key=SecretStr("not-a-real-secret"),
    )


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def stubs() -> Stubs:
    return Stubs()


@pytest.fixture
def client(settings: Settings, common: CommonSettings, stubs: Stubs) -> SqsSnsClient:
    return SqsSnsClient(settings, common=common, client_factory=stubs.factory())


@pytest.fixture
def read_api(client: SqsSnsClient, common: CommonSettings, settings: Settings) -> SqsSnsReadApi:
    return SqsSnsReadApi(client, common, settings)
