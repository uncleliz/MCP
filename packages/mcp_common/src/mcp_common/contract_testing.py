"""Contract-first verification helpers (ADR-0013) shared by every server's
`tests/test_contract.py`.

Test-support only: `yaml` / `jsonschema` are imported lazily (they are workspace dev
dependencies, not runtime dependencies of any server).

* :func:`load_contract` / :func:`operations_by_id` — read `api-contract.yaml`.
* :func:`build_snapshot` — the `tools.snapshot.json` payload of a live `FastMCP` server.
* :func:`assert_snapshot_matches_contract` — a server's tool set and each tool's
  `inputSchema` (property names, required, and the constraints the contract states) must
  equal the contract operations tagged for that server.
* :func:`validate_structured_content` — a tool's `structuredContent` must validate
  against the contract's response schema (200) or `ErrorEnvelope`, plus the invariants
  JSON Schema cannot express (`assert_envelope_invariants`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_common.envelope import ToolResult
from mcp_common.testing import assert_envelope_invariants

__all__ = [
    "CONTRACT_RELATIVE_PATH",
    "find_contract_path",
    "load_contract",
    "operations_by_id",
    "build_snapshot",
    "assert_snapshot_matches_contract",
    "validate_structured_content",
]

CONTRACT_RELATIVE_PATH = Path("docs/squad/mcp-data-platform/api-contract.yaml")
_CONSTRAINT_KEYS = ("minimum", "maximum", "minLength", "maxLength", "maxItems", "pattern", "enum")


def find_contract_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / CONTRACT_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{CONTRACT_RELATIVE_PATH} not found above {__file__}")


def load_contract(path: Path | None = None) -> dict[str, Any]:
    import yaml

    with (path or find_contract_path()).open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
    return loaded


def operations_by_id(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    for path_item in contract["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operations[operation["operationId"]] = operation
    return operations


def _resolve(contract: dict[str, Any], node: Any) -> Any:
    """Resolve a (possibly nested) local `$ref` at the top of `node`."""
    while isinstance(node, dict) and "$ref" in node:
        target: Any = contract
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


async def build_snapshot(mcp: FastMCP) -> dict[str, dict[str, Any]]:
    """`tools.snapshot.json` content: tool name -> description + inputSchema."""
    tools = await mcp.list_tools()
    return {
        tool.name: {"description": tool.description, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda t: t.name)
    }


def _flatten_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Collapse pydantic's `anyOf: [X, {type: null}]` and `type: [X, null]` to X."""
    schema = dict(schema)
    variants = schema.pop("anyOf", None)
    if variants:
        non_null = [v for v in variants if v.get("type") != "null"]
        if len(non_null) == 1:
            merged = dict(non_null[0])
            merged.update(schema)
            schema = merged
    return schema


def _constraints(schema: dict[str, Any], contract: dict[str, Any] | None = None) -> dict[str, Any]:
    flat = _flatten_nullable(schema)
    found = {key: flat[key] for key in _CONSTRAINT_KEYS if key in flat}
    if "enum" in found:
        found["enum"] = sorted(v for v in found["enum"] if v is not None)
    if flat.get("type") == "array" and "items" in flat:
        # `items: {$ref: '#/components/schemas/SourceType'}` must compare by its resolved enum.
        items = _resolve(contract, flat["items"]) if contract is not None else flat["items"]
        item_constraints = _constraints(items, contract)
        if item_constraints:
            found["items"] = item_constraints
    return found


def assert_snapshot_matches_contract(
    snapshot: dict[str, Any], contract: dict[str, Any], *, tag: str
) -> None:
    """Fail with every mismatch between `snapshot` and the contract operations tagged `tag`."""
    problems: list[str] = []
    expected = {
        op_id: op
        for op_id, op in operations_by_id(contract).items()
        if tag in op.get("tags", []) and op.get("x-interface") != "cli"
    }
    if set(snapshot) != set(expected):
        problems.append(
            f"tool set differs: only in snapshot={sorted(set(snapshot) - set(expected))}, "
            f"only in contract={sorted(set(expected) - set(snapshot))}"
        )
    for name in sorted(set(snapshot) & set(expected)):
        contract_schema = _resolve(
            contract, expected[name]["requestBody"]["content"]["application/json"]["schema"]
        )
        snap_schema = snapshot[name]["inputSchema"]
        c_props = contract_schema.get("properties", {})
        s_props = snap_schema.get("properties", {})
        if set(c_props) != set(s_props):
            problems.append(f"{name}: input properties {sorted(s_props)} != {sorted(c_props)}")
            continue
        if set(contract_schema.get("required", [])) != set(snap_schema.get("required", [])):
            problems.append(
                f"{name}: required {sorted(snap_schema.get('required', []))} != "
                f"{sorted(contract_schema.get('required', []))}"
            )
        for prop, c_def in c_props.items():
            want = _constraints(_resolve(contract, c_def), contract)
            got = _constraints(s_props[prop])
            if want != got:
                problems.append(f"{name}.{prop}: constraints {got} != contract {want}")
            c_flat = _flatten_nullable(_resolve(contract, c_def))
            # A default on a *required* property is meaningless (the contract's shared
            # `GitLabProjectRef` carries `default: null` even where it is required).
            if (
                "default" in c_flat
                and prop not in contract_schema.get("required", [])
                and c_flat["default"] != _flatten_nullable(s_props[prop]).get("default")
            ):
                problems.append(
                    f"{name}.{prop}: default {_flatten_nullable(s_props[prop]).get('default')!r}"
                    f" != contract {c_flat['default']!r}"
                )
    if problems:
        raise AssertionError("snapshot vs contract:\n" + "\n".join(f"- {p}" for p in problems))


def validate_structured_content(
    contract: dict[str, Any], operation_id: str, payload: dict[str, Any], *, is_error: bool = False
) -> None:
    """Validate a tool's `structuredContent` against the contract for `operation_id`."""
    from jsonschema import Draft202012Validator

    if is_error:
        schema: dict[str, Any] = {"$ref": "#/components/schemas/ErrorEnvelope"}
    else:
        response = _resolve(contract, operations_by_id(contract)[operation_id]["responses"]["200"])
        schema = copy.deepcopy(response["content"]["application/json"]["schema"])
    root = {"components": contract["components"], **schema}
    errors = sorted(
        Draft202012Validator(root).iter_errors(payload), key=lambda e: list(e.absolute_path)
    )
    if errors:
        raise AssertionError(
            f"{operation_id}: structuredContent violates the contract:\n"
            + "\n".join(
                f"- {'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:10]
            )
        )
    if not is_error:
        assert_envelope_invariants(ToolResult.model_validate(payload))
        for citation in payload["citations"]:
            uri = citation.get("uri")
            if uri is not None and not uri.startswith(("http://", "https://")):
                raise AssertionError(f"{operation_id}: citation uri is not a URL: {uri}")
        for item in payload["items"]:
            for key in ("url", "web_url"):
                if key in item and item[key] is not None:
                    if not str(item[key]).startswith(("http://", "https://")):
                        raise AssertionError(f"{operation_id}: {key} is not a URL: {item[key]}")
