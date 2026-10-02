"""T-069: connector framework, registry and the "client.py only, never read_api.py" rule.

AC: FR-012/AC-001, FR-014/AC-001 (ADR-0012 A4 / ADR-0007 A3).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from ingest_helpers import FakeConnector
from mcp_confluence.client import ALLOWED_OPERATIONS as CONFLUENCE_OPS
from mcp_gitlab.client import ALLOWED_OPERATIONS as GITLAB_OPS
from mcp_ingest.connectors import registry
from mcp_ingest.connectors.base import AsyncBridge, ConnectorStatus, SourceConnector
from mcp_ingest.connectors.confluence import ConfluenceConnector
from mcp_ingest.connectors.gitlab import GitLabConnector
from mcp_ingest.connectors.jira import JiraConnector
from mcp_ingest.connectors.opensearch import OpenSearchConnector
from mcp_ingest.settings import Settings
from mcp_jira.client import ALLOWED_OPERATIONS as JIRA_OPS
from mcp_opensearch.client import ALLOWED_OPERATIONS as OPENSEARCH_OPS

import mcp_ingest

PACKAGE = Path(mcp_ingest.__file__).parent
CONNECTORS = PACKAGE / "connectors"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def test_ADR_0012_A4_no_connector_module_imports_read_api() -> None:
    offenders = {
        path.name: sorted(
            m for m in imported_modules(path) if m.endswith("read_api") or ".read_api" in m
        )
        for path in CONNECTORS.glob("*.py")
    }
    assert {name: mods for name, mods in offenders.items() if mods} == {}


def test_no_ingest_module_imports_a_read_api_or_a_tools_layer() -> None:
    for path in PACKAGE.rglob("*.py"):
        bad = [
            m
            for m in imported_modules(path)
            if m.split(".")[-1] in {"read_api", "tools", "server"} and m.startswith("mcp_")
        ]
        assert bad == [], f"{path}: {bad}"


def test_the_isolation_check_would_catch_a_violation(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "from mcp_gitlab.read_api import GitLabReadApi\nimport mcp_confluence.read_api\n"
    )
    found = imported_modules(sample)
    assert "mcp_gitlab.read_api" in found and "mcp_confluence.read_api" in found


def test_FR_012_AC_001_the_registry_lists_the_v1_connectors_plus_jira() -> None:
    assert registry.source_names() == ["confluence", "gitlab", "opensearch", "jira"]
    specs = registry.REGISTRY()
    assert {n: s.class_name for n, s in specs.items()} == {
        "confluence": "ConfluenceConnector", "gitlab": "GitLabConnector",
        "opensearch": "OpenSearchConnector", "jira": "JiraConnector",
    }  # fmt: skip
    described = {spec.source_type: status for spec, status in registry.describe_all(Settings())}
    assert all(isinstance(status, ConnectorStatus) for status in described.values())


def test_a_connector_satisfies_the_protocol() -> None:
    assert isinstance(FakeConnector(), SourceConnector)
    for cls in (ConfluenceConnector, GitLabConnector, OpenSearchConnector, JiraConnector):
        for member in (
            "source_type",
            "name",
            "operations_used",
            "iter_documents",
            "fetch_documents",
            "close",
            "status",
        ):
            assert hasattr(cls, member), (cls, member)


@pytest.mark.parametrize(
    ("connector", "allowlist"),
    [
        (ConfluenceConnector, CONFLUENCE_OPS),
        (GitLabConnector, GITLAB_OPS),
        (OpenSearchConnector, OPENSEARCH_OPS),
        (JiraConnector, JIRA_OPS),
    ],
)
def test_every_client_operation_a_connector_calls_is_in_the_source_read_only_allowlist(
    connector: type, allowlist: tuple[str, ...]
) -> None:
    assert connector.operations_used and set(connector.operations_used) <= set(allowlist)
    assert all(op.startswith(("GET ", "POST /{index}/_search")) for op in connector.operations_used)


def test_the_registry_builds_connectors_lazily(monkeypatch) -> None:
    for name in ("MCP_CONFLUENCE_BASE_URL", "MCP_GITLAB_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    # Describing must not need credentials nor build an HTTP client.
    assert registry.describe_all(Settings())
    with pytest.raises(Exception, match="MCP_CONFLUENCE"):
        registry.build("confluence", Settings())


def test_async_bridge_runs_coroutines_on_one_loop_and_closes_cleanly() -> None:
    import asyncio

    bridge = AsyncBridge()
    loops = set()

    async def which() -> int:
        loops.add(id(asyncio.get_running_loop()))
        return 7

    assert bridge.run(which()) == 7 and bridge.run(which()) == 7 and len(loops) == 1
    done: list[bool] = []

    async def finalize() -> None:
        done.append(True)

    bridge.close(finalize())
    assert done == [True]
    never_used = AsyncBridge()
    coro = finalize()
    never_used.close(coro)  # no loop was ever created: the coroutine is discarded, not awaited
    assert done == [True]


def test_parse_ts_normalises_and_rejects() -> None:
    from datetime import UTC, datetime

    from mcp_ingest.connectors._common import parse_ts

    assert parse_ts("2026-09-30T10:00:00.000Z") == datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert parse_ts("2026-09-30T17:00:00+07:00") == datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert parse_ts("2026-09-30T10:00:00") == datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert parse_ts("yesterday") is None and parse_ts(None) is None and parse_ts("") is None
