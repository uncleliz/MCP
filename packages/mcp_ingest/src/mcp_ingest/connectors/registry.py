"""Connector registry (T-069). Factories are lazy so that listing the connectors never builds an
HTTP client or reads a credential; a connector is only constructed when it is going to crawl.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp_ingest.connectors.base import ConnectorStatus, SourceConnector
from mcp_ingest.settings import Settings

__all__ = ["REGISTRY", "ConnectorSpec", "build", "describe_all", "source_names"]


class ConnectorSpec:
    def __init__(
        self,
        source_type: str,
        class_name: str,
        status: Callable[[Settings], ConnectorStatus],
        factory: Callable[[Settings], SourceConnector],
    ) -> None:
        self.source_type = source_type
        self.class_name = class_name
        self.status = status
        self.factory = factory


def _specs() -> dict[str, ConnectorSpec]:
    from mcp_ingest.connectors import confluence, gitlab, jira, opensearch

    return {
        "confluence": ConnectorSpec(
            "confluence", "ConfluenceConnector", confluence.status, confluence.build
        ),
        "gitlab": ConnectorSpec("gitlab", "GitLabConnector", gitlab.status, gitlab.build),
        "opensearch": ConnectorSpec(
            "opensearch", "OpenSearchConnector", opensearch.status, opensearch.build
        ),
        "jira": ConnectorSpec("jira", "JiraConnector", jira.status, jira.build),
    }


REGISTRY = _specs  # callable on purpose: imports the connector modules on first use


def source_names() -> list[str]:
    return list(_specs())


def describe_all(settings: Settings) -> list[tuple[ConnectorSpec, ConnectorStatus]]:
    return [(spec, spec.status(settings)) for spec in _specs().values()]


def build(source_type: str, settings: Settings) -> SourceConnector:
    return _specs()[source_type].factory(settings)
