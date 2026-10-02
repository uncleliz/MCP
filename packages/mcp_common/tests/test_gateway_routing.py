"""T-109 (E7, ADR-0021) — gateway routing, auth-context and audit (FR-022/AC-001, TC-101).

Each test is named after the acceptance criterion it proves.
"""

from __future__ import annotations

import json

import pytest
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.gateway import (
    GATEWAY_SOURCE,
    AuthContext,
    Gateway,
    HttpTransportAdapter,
    StdioTransportAdapter,
)


def _gateway(**kwargs: object) -> Gateway:
    # High capacity so routing tests are never incidentally rate-limited.
    return Gateway(rate_limit_capacity=1000, rate_limit_refill_per_s=1000, **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_AC_001_routes_valid_call_to_correct_server_and_returns_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, object] = {}

    async def knowledge_search(args, auth):  # type: ignore[no-untyped-def]
        seen["args"] = dict(args)
        seen["server"] = "knowledge"
        return {"items": ["doc-1"]}

    async def jira_search(args, auth):  # type: ignore[no-untyped-def]
        seen["server"] = "jira"
        return {"issues": []}

    gw = _gateway()
    gw.register("mcp-knowledge", "search_company_knowledge", knowledge_search)
    gw.register("mcp-jira", "jira_search_issues", jira_search)

    result = await gw.dispatch("search_company_knowledge", {"q": "payments"})

    assert result == {"items": ["doc-1"]}
    # Routed to the correct server (not the other registered one).
    assert seen["server"] == "knowledge"
    assert seen["args"] == {"q": "payments"}


@pytest.mark.asyncio
async def test_AC_001_writes_append_only_audit_entry_to_stderr_with_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return {"ok": True}

    gw = _gateway()
    gw.register("mcp-knowledge", "get_service", handler)

    await gw.dispatch("get_service", {"name": "payments"}, AuthContext(process_principal="alice"))

    captured = capsys.readouterr()
    # Audit goes to stderr, never stdout (R2/NFR-012).
    assert captured.out == ""
    record = json.loads(captured.err.strip().splitlines()[-1])
    assert record["status"] == "routed"
    assert record["tool"] == "get_service"
    assert record["server"] == GATEWAY_SOURCE
    # Caller identity is on the audit record.
    assert record["request_id"] == "alice"


@pytest.mark.asyncio
async def test_AC_001_audit_logs_arguments_only_as_hash_at_info_not_raw(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return {"ok": True}

    gw = _gateway()
    gw.register("mcp-knowledge", "search_code", handler)

    await gw.dispatch("search_code", {"query": "super-secret-marker-value"})

    captured = capsys.readouterr()
    # The raw argument value never appears in the INFO audit stream — only a hash.
    assert "super-secret-marker-value" not in captured.err
    record = json.loads(captured.err.strip().splitlines()[-1])
    assert "args_hash=" in record["message"]
    assert record["message"].split("args_hash=")[1] != "none"


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_with_invalid_input_and_audited(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gw = _gateway()

    with pytest.raises(ToolError) as exc:
        await gw.dispatch("no_such_tool", {})

    assert exc.value.code == ErrorCode.INVALID_INPUT
    record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert record["status"] == "error"
    assert record["error_code"] == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_tool_error_from_handler_propagates_and_is_audited(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failing(args, auth):  # type: ignore[no-untyped-def]
        raise ToolError(
            ErrorCode.UPSTREAM_TIMEOUT, "boom", "mcp-knowledge", retryable=True
        )

    gw = _gateway()
    gw.register("mcp-knowledge", "find_related_knowledge", failing)

    with pytest.raises(ToolError) as exc:
        await gw.dispatch("find_related_knowledge", {})

    assert exc.value.code == ErrorCode.UPSTREAM_TIMEOUT
    record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert record["status"] == "error"
    assert record["error_code"] == ErrorCode.UPSTREAM_TIMEOUT.value


def test_duplicate_tool_registration_is_refused() -> None:
    gw = _gateway()

    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return None

    gw.register("mcp-knowledge", "get_service", handler)
    with pytest.raises(ValueError, match="already routed"):
        gw.register("mcp-jira", "get_service", handler)


def test_tool_surface_is_the_unified_discoverable_map() -> None:
    gw = _gateway()

    async def handler(args, auth):  # type: ignore[no-untyped-def]
        return None

    gw.register("mcp-knowledge", "search_company_knowledge", handler)
    gw.register("mcp-jira", "jira_get_issue", handler)

    assert gw.tool_surface() == {
        "search_company_knowledge": "mcp-knowledge",
        "jira_get_issue": "mcp-jira",
    }


def test_auth_context_v1_is_process_owner_with_reserved_request_slot() -> None:
    ctx = AuthContext.for_process_owner()
    # v1: identity = process owner; the per-request slot (v1.1) is empty.
    assert ctx.request_principal is None
    assert ctx.effective_principal == ctx.process_principal

    # v1.1 forward-compat: a per-request principal, when set, becomes effective — but
    # this is only the data slot; no per-request transport is implemented at v1.
    v11 = AuthContext(process_principal="svc", request_principal="bob")
    assert v11.effective_principal == "bob"


@pytest.mark.asyncio
async def test_dispatch_defaults_auth_to_process_owner() -> None:
    captured_auth: dict[str, object] = {}

    async def handler(args, auth):  # type: ignore[no-untyped-def]
        captured_auth["principal"] = auth.effective_principal
        return None

    gw = _gateway()
    gw.register("mcp-knowledge", "get_repository", handler)
    await gw.dispatch("get_repository", {})

    assert captured_auth["principal"]  # non-empty process-owner identity


def test_default_transport_is_stdio_and_opens_no_port() -> None:
    gw = _gateway()
    assert isinstance(gw.transport, StdioTransportAdapter)
    assert gw.transport.opens_network_port() is False


def test_http_transport_seam_is_not_implemented_at_v1() -> None:
    # The seam documents v1.1 but refuses to construct — v1 opens no network port.
    with pytest.raises(NotImplementedError, match="v1.1"):
        HttpTransportAdapter()
