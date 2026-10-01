"""T-059/T-061 / NFR-001 / TC-037: the read-only surface of mcp-sqs-sns (FR-010/AC-003,
FR-014/AC-001, FR-014/AC-002). `ReceiveMessage` mutates the visibility timeout, so *no code path*
may call it; that is asserted on the source itself, not just on the allowlist.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_common.testing import (
    assert_readonly_tool_surface,
    assert_unknown_tool_rejected_at_protocol_layer,
)
from mcp_common.tooling import registered_tool_functions
from mcp_sqs_sns.client import ALLOWED_OPERATIONS
from mcp_sqs_sns.read_api import SqsSnsReadApi
from mcp_sqs_sns.server import build_server
from sqs_helpers import OK_CALLS, Stubs, stub_ok

import mcp_sqs_sns

PACKAGE_DIR = Path(mcp_sqs_sns.__file__).parent
CONTRACT = load_contract()

# boto3 client methods with a side effect (including ReceiveMessage: visibility timeout).
SIDE_EFFECT_METHODS = {
    "receive_message", "send_message", "send_message_batch", "delete_message",
    "delete_message_batch", "change_message_visibility", "change_message_visibility_batch",
    "purge_queue", "create_queue", "delete_queue", "set_queue_attributes", "tag_queue",
    "untag_queue", "add_permission", "remove_permission", "publish", "publish_batch",
    "create_topic", "delete_topic", "set_topic_attributes", "subscribe", "unsubscribe",
    "confirm_subscription", "tag_resource", "untag_resource",
}  # fmt: skip


@pytest.fixture
def server(read_api: SqsSnsReadApi):
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
        "sqs_send_message", "sqs_receive_message", "sqs_delete_message", "sqs_purge_queue",
        "sqs_delete_queue", "sqs_create_queue", "sns_publish", "sns_subscribe",
        "sns_unsubscribe", "sns_delete_topic",
    ],
)  # fmt: skip
@pytest.mark.asyncio
async def test_FR_014_AC_002_unknown_write_tool_rejected_at_protocol_layer(
    server, write_tool: str
) -> None:
    await assert_unknown_tool_rejected_at_protocol_layer(server, write_tool, {"queue_name": "x"})


@pytest.mark.asyncio
async def test_every_tool_call_only_uses_allowlisted_aws_operations(server, stubs: Stubs) -> None:
    seen: list[str] = []
    for client in stubs.clients.values():
        client.meta.events.register_first(
            "before-parameter-build.*.*",
            lambda model, **_k: seen.append(f"{model.service_model.service_name}:{model.name}"),
        )
    async with create_connected_server_and_client_session(server) as session:
        for name, args in OK_CALLS:
            stub_ok(stubs, name)
            result = await session.call_tool(name, arguments=args)
            assert not result.isError, (name, result.content)
    assert seen and set(seen) <= set(ALLOWED_OPERATIONS), set(seen) - set(ALLOWED_OPERATIONS)
    assert "sqs:ReceiveMessage" not in seen
    stubs.assert_all_consumed()


def _source_files() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def test_TC_037_no_code_path_calls_receive_message_or_any_side_effect_method() -> None:
    offenders: list[str] = []
    for path in _source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in SIDE_EFFECT_METHODS:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in SIDE_EFFECT_METHODS:
                    offenders.append(f"{path.name}:{node.lineno}: {node.func.id}()")
    assert offenders == []


def _string_literals(path: Path) -> list[str]:
    """String constants of a module, excluding docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_TC_037_receive_message_is_never_a_dispatch_target_or_allowlist_entry() -> None:
    from mcp_sqs_sns import client

    assert "sqs:ReceiveMessage" not in ALLOWED_OPERATIONS
    assert "receive_message" not in client._METHODS.values()  # noqa: SLF001
    # The only literal mention of the action is the IAM-simulation *warning* list (settings.py).
    mentions = {
        p.name
        for p in _source_files()
        if any(
            s in {"sqs:ReceiveMessage", "ReceiveMessage", "receive_message"}
            for s in _string_literals(p)
        )
    }
    assert mentions == {"settings.py"}
