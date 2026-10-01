"""T-045 / NFR-001: the read-only surface of mcp-cloudwatch (FR-014/AC-001, FR-014/AC-002)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cw_helpers import OK_CALLS, Stubs, stub_ok
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_cloudwatch.client import ALLOWED_OPERATIONS
from mcp_cloudwatch.read_api import CloudWatchReadApi
from mcp_cloudwatch.server import build_server
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions

import mcp_cloudwatch

PACKAGE_DIR = Path(mcp_cloudwatch.__file__).parent
CONTRACT = load_contract()


@pytest.fixture
def server(read_api: CloudWatchReadApi):
    return build_server(read_api)


def test_FR_014_AC_001_tool_surface_is_readonly_and_in_contract(server) -> None:
    snapshot = json.loads((PACKAGE_DIR / "tools.snapshot.json").read_text(encoding="utf-8"))
    assert_readonly_tool_surface(
        registered_tools=registered_tool_functions(server),
        contract_operations=operations_by_id(CONTRACT),
        snapshot=snapshot,
        client_allowlist=ALLOWED_OPERATIONS,
    )


@pytest.mark.parametrize(
    "write_tool",
    [
        "cloudwatch_put_metric_data",
        "cloudwatch_delete_alarms",
        "cloudwatch_set_alarm_state",
        "cloudwatch_put_log_events",
        "cloudwatch_delete_log_group",
        "cloudwatch_create_log_group",
    ],
)
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"name": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_uses_allowlisted_aws_operations(server, stubs: Stubs) -> None:
    seen: list[str] = []
    for service, client in stubs.clients.items():
        client.meta.events.register_first(
            "before-parameter-build.*.*",
            lambda model, service=service, **_k: seen.append(
                f"{model.service_model.service_name}:{model.name}"
            ),
        )
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            stub_ok(stubs, name)
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    assert seen and set(seen) <= set(ALLOWED_OPERATIONS), set(seen) - set(ALLOWED_OPERATIONS)
    stubs.assert_all_consumed()


def test_source_never_calls_a_write_api() -> None:
    pattern = re.compile(
        r"\.(put_|delete_|create_|update_|set_alarm|enable_|disable_|tag_|untag_|associate_|"
        r"disassociate_|start_live|import_)\w*\("
    )
    offenders: list[str] = []
    for path in PACKAGE_DIR.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
