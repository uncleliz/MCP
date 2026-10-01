"""T-033: read-only guards of mcp_opensearch.client (transport, endpoints, body, startup)."""

from __future__ import annotations

import copy

import pytest
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_opensearch.client import (
    ALLOWED_OPERATIONS,
    ALLOWED_TOP_LEVEL_KEYS,
    OpenSearchClient,
    ReadOnlyTransport,
    assert_body_allowed,
    assert_request_allowed,
    to_tool_error,
)
from mcp_opensearch.settings import Settings
from opensearchpy import exceptions as osx
from os_helpers import AUTHINFO_ADMIN, AUTHINFO_RO, FORBIDDEN_BODIES, INDEX, FakeOpenSearch
from pydantic import SecretStr

# -- body guard: FR-004/AC-002, FR-014/AC-001, ADR-0008 A2 (TC-018) ------------------------


@pytest.mark.parametrize("name", sorted(FORBIDDEN_BODIES))
def test_FR_004_AC_002_forbidden_constructs_are_not_permitted_at_any_depth(name: str) -> None:
    body = FORBIDDEN_BODIES[name]
    with pytest.raises(NotPermittedError) as exc:
        assert_body_allowed(body)
    error = exc.value
    assert error.code == ErrorCode.NOT_PERMITTED and error.source == "opensearch"
    assert error.details["operation"].startswith("body.")
    assert set(error.details["allowlist"]) == set(ALLOWED_TOP_LEVEL_KEYS)


def test_not_permitted_reports_the_path_of_the_offending_key() -> None:
    with pytest.raises(NotPermittedError) as exc:
        assert_body_allowed({"query": {"bool": {"filter": [{"x": 1}, {"script": {}}]}}})
    assert exc.value.details["operation"] == "body.query.bool.filter[1].script"


def test_safe_bodies_pass() -> None:
    assert_body_allowed(
        {
            "query": {"bool": {"must": [{"match": {"service": "payment"}}], "filter": [
                {"range": {"@timestamp": {"gte": "2026-09-30T10:00:00Z"}}}]}},
            "sort": [{"@timestamp": {"order": "desc"}}],
            "_source": ["a"], "aggs": {"t": {"terms": {"field": "x", "size": 5}}},
            "highlight": {"fields": {"*": {}}}, "size": 5, "from": 0,
            "search_after": [1, "a"], "track_total_hits": False, "timeout": "20s",
        }
    )  # fmt: skip
    assert_body_allowed({"query": {"terms": {"level": ["ERROR", "WARN"]}}})  # plain terms


def test_unknown_top_level_key_is_not_permitted() -> None:
    with pytest.raises(NotPermittedError) as exc:
        assert_body_allowed({"query": {}, "collapse": {"field": "x"}})
    assert exc.value.details["operation"] == "body.collapse"


def test_body_is_not_mutated_by_the_guard() -> None:
    body = {"query": {"match_all": {}}}
    before = copy.deepcopy(body)
    assert_body_allowed(body)
    assert body == before


# -- endpoint guard ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/"), ("GET", "/_cat/indices/app-logs-*"), ("GET", "/_cat/indices"),
        ("GET", f"/{INDEX}/_mapping"), ("POST", f"/{INDEX}/_search"),
        ("POST", "/app-logs-*/_count"), ("GET", "/_plugins/_security/authinfo"),
        ("HEAD", "/"),
    ],
)  # fmt: skip
def test_allowed_endpoints(method: str, url: str) -> None:
    assert_request_allowed(method, url, None)


@pytest.mark.parametrize(
    ("method", "url", "params"),
    [
        ("PUT", "/idx/_doc/1", None), ("POST", "/idx/_doc", None), ("DELETE", "/idx", None),
        ("POST", "/_bulk", None), ("POST", "/idx/_update/1", None),
        ("POST", "/idx/_delete_by_query", None), ("POST", "/_reindex", None),
        ("POST", "/_search/scroll", None), ("GET", "/_search/scroll", None),
        ("DELETE", "/_search/scroll", None), ("POST", "/idx/_search/point_in_time", None),
        ("POST", "/idx/_pit", None), ("POST", "/idx/_search", {"scroll": "1m"}),
        ("GET", "/_cluster/settings", None), ("GET", "/_security/user", None),
        ("GET", "/_plugins/_security/api/internalusers", None),
        ("POST", "/idx/_forcemerge", None), ("PUT", "/_cluster/settings", None),
    ],
)  # fmt: skip
def test_FR_014_AC_001_everything_else_is_not_permitted(method: str, url: str, params) -> None:
    with pytest.raises(NotPermittedError):
        assert_request_allowed(method, url, params)


@pytest.mark.asyncio
async def test_transport_refuses_write_before_any_io() -> None:
    transport = ReadOnlyTransport(["https://127.0.0.1:1"])
    for method, url in [("PUT", "/x/_doc/1"), ("DELETE", "/x"), ("POST", "/_search/scroll")]:
        with pytest.raises(NotPermittedError):
            await transport.perform_request(method, url, body=b"{}")
    await transport.close()


def test_allowlist_constants_are_read_only() -> None:
    assert all(op.split()[0] in {"GET", "POST"} for op in ALLOWED_OPERATIONS)
    posts = [op for op in ALLOWED_OPERATIONS if op.startswith("POST")]
    assert sorted(posts) == ["POST /{index}/_count", "POST /{index}/_search"]


# -- client wrapper ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_search_applies_body_guard_before_the_sdk(
    client: OpenSearchClient, fake: FakeOpenSearch
) -> None:
    with pytest.raises(NotPermittedError):
        await client.search("idx", {"query": {"script": {}}})
    assert fake.calls == []  # never reached the SDK
    await client.search("idx", {"query": {"match_all": {}}}, request_timeout=5)
    assert fake.last("search")["params"]["request_timeout"] == 5


@pytest.mark.asyncio
async def test_client_methods_go_through_the_sdk_with_expected_arguments(
    client: OpenSearchClient, fake: FakeOpenSearch
) -> None:
    await client.count("app-logs-*", {"query": {"match_all": {}}})
    assert fake.last("count")["index"] == "app-logs-*"
    await client.cat_indices("app-*")
    assert fake.last("cat.indices")["params"]["format"] == "json"
    await client.get_mapping(INDEX)
    assert fake.last("indices.get_mapping")["index"] == INDEX


@pytest.mark.asyncio
async def test_client_aclose_closes_sdk(client: OpenSearchClient, fake: FakeOpenSearch) -> None:
    await client.aclose()
    assert fake.closed


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (osx.ConnectionTimeout("TIMEOUT", "t", Exception("x")), ErrorCode.UPSTREAM_TIMEOUT, True),
        (
            osx.ConnectionError("N/A", "refused", Exception("x")),
            ErrorCode.UPSTREAM_UNAVAILABLE,
            True,
        ),
        (osx.SSLError("N/A", "tls", Exception("x")), ErrorCode.UPSTREAM_UNAVAILABLE, True),
        (osx.AuthenticationException(401, "unauthorized", {}), ErrorCode.UNAUTHORIZED, False),
        (osx.AuthorizationException(403, "forbidden", {}), ErrorCode.FORBIDDEN, False),
        (osx.TransportError(429, "too many", {}), ErrorCode.RATE_LIMITED, True),
        (osx.TransportError(503, "unavailable", {}), ErrorCode.UPSTREAM_UNAVAILABLE, True),
        (osx.TransportError(500, "boom", {}), ErrorCode.UPSTREAM_ERROR, True),
    ],
)
def test_sdk_exceptions_map_to_contract_error_codes(exc, code, retryable) -> None:
    error = to_tool_error(exc, host="opensearch.example.test")
    assert error.code == code and error.retryable is retryable and error.source == "opensearch"
    if code in {ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE}:
        assert "VPN" in error.details["hint"] and "opensearch.example.test" in error.details["hint"]


def test_not_found_and_bad_query_mapping() -> None:
    nf = to_tool_error(
        osx.NotFoundError(404, "index_not_found_exception", {"error": {"type": "x"}}), host="h"
    )
    assert nf.code == ErrorCode.UPSTREAM_ERROR and nf.details["upstream_status"] == 404
    bad = to_tool_error(osx.RequestError(400, "parse_exception", {"error": {"reason": "bad"}}), "h")
    assert bad.code == ErrorCode.INVALID_INPUT and bad.details["field"] == "query"
    other = to_tool_error(RuntimeError("x"), "h")
    assert other.code == ErrorCode.INTERNAL


@pytest.mark.asyncio
async def test_sdk_errors_become_tool_errors_in_the_wrapper(
    client: OpenSearchClient, fake: FakeOpenSearch
) -> None:
    fake.responses["search"] = osx.ConnectionTimeout("TIMEOUT", "t", Exception("x"))
    with pytest.raises(ToolError) as exc:
        await client.search("idx", {"query": {}})
    assert exc.value.code == ErrorCode.UPSTREAM_TIMEOUT


def test_default_sdk_client_uses_read_only_transport_timeout_and_retry(
    settings: Settings,
) -> None:
    client = OpenSearchClient(settings)
    sdk = client._os
    assert isinstance(sdk.transport, ReadOnlyTransport)
    assert sdk.transport.max_retries == 1 and sdk.transport.retry_on_timeout is False
    assert sdk.transport.kwargs["timeout"] == 7.0


# -- startup check ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_check_green_for_readonly_roles(client: OpenSearchClient) -> None:
    report = await client.verify_credentials()
    assert report.ok and report.user == "mcp_ro" and report.reasons == []
    assert await client.credential_check() is True


@pytest.mark.asyncio
async def test_startup_check_refuses_write_capable_roles(
    client: OpenSearchClient, fake: FakeOpenSearch
) -> None:
    fake.responses["transport GET /_plugins/_security/authinfo"] = AUTHINFO_ADMIN
    report = await client.verify_credentials()
    assert not report.ok and "all_access" in report.reasons[0]
    assert await client.credential_check() is False


@pytest.mark.parametrize(
    "authinfo",
    [
        osx.NotFoundError(404, "no handler", {}),
        osx.AuthorizationException(403, "forbidden", {}),
        {"user_name": "x", "roles": []},
        ["not-a-dict"],
    ],
)
@pytest.mark.asyncio
async def test_startup_check_fails_closed_when_roles_cannot_be_read(
    client: OpenSearchClient, fake: FakeOpenSearch, authinfo
) -> None:
    fake.responses["transport GET /_plugins/_security/authinfo"] = authinfo
    report = await client.verify_credentials()
    assert not report.ok and "cannot verify read-only" in report.reasons[0]


@pytest.mark.asyncio
async def test_startup_check_reports_connectivity_and_auth_failure(
    client: OpenSearchClient, fake: FakeOpenSearch
) -> None:
    fake.responses["info"] = osx.AuthenticationException(401, "unauthorized", {})
    report = await client.verify_credentials()
    assert not report.ok and "unauthorized" in report.reasons[0]
    fake.responses["info"] = osx.ConnectionError("N/A", "refused", Exception("x"))
    report = await client.verify_credentials()
    assert "upstream_unavailable" in report.reasons[0]


@pytest.mark.asyncio
async def test_deny_roles_are_configurable(settings: Settings, common, fake) -> None:
    custom = Settings(
        hosts=settings.hosts, password=SecretStr("p"), username="u", deny_roles="readall"
    )
    c = OpenSearchClient(custom, common=common, os_client=fake)
    fake.responses["transport GET /_plugins/_security/authinfo"] = AUTHINFO_RO
    report = await c.verify_credentials()
    assert not report.ok and "readall" in report.reasons[0]
