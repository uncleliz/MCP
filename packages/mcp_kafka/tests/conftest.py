"""Shared fixtures for mcp-kafka tests: an in-memory cluster behind the real adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp_common.config import CommonSettings
from mcp_kafka.client import ConfluentKafkaReader, KafkaClient
from mcp_kafka.read_api import KafkaReadApi
from mcp_kafka.settings import Settings
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).parent))

from kafka_helpers import FakeAdmin, FakeCluster, FakeConsumer, make_cluster  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(bootstrap_servers="kafka.example.test:9092")


@pytest.fixture
def sasl_settings() -> Settings:
    return Settings(
        bootstrap_servers="kafka.example.test:9093",
        security_protocol="SASL_SSL",
        sasl_username="mcp_ro",
        sasl_password=SecretStr("kafka-pw-not-real"),
    )


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings()


@pytest.fixture
def cluster() -> FakeCluster:
    return make_cluster()


@pytest.fixture
def reader(settings: Settings, cluster: FakeCluster) -> ConfluentKafkaReader:
    return ConfluentKafkaReader(
        settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )


@pytest.fixture
def sasl_reader(sasl_settings: Settings, cluster: FakeCluster) -> ConfluentKafkaReader:
    return ConfluentKafkaReader(
        sasl_settings,
        admin_factory=lambda conf: FakeAdmin(conf, cluster),
        consumer_factory=lambda conf: FakeConsumer(conf, cluster),
    )


@pytest.fixture
def sasl_client(
    sasl_settings: Settings, common: CommonSettings, sasl_reader: ConfluentKafkaReader
) -> KafkaClient:
    return KafkaClient(sasl_settings, common=common, reader=sasl_reader)


@pytest.fixture
def client(settings: Settings, common: CommonSettings, reader: ConfluentKafkaReader) -> KafkaClient:
    return KafkaClient(settings, common=common, reader=reader)


@pytest.fixture
def read_api(client: KafkaClient, common: CommonSettings) -> KafkaReadApi:
    return KafkaReadApi(client, common)
