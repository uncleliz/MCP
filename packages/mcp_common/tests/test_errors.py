"""T-007: mcp_common.errors — ErrorCode taxonomy + exception -> ToolError mapper.

One test per row of the exception -> code mapping table (Done-when of T-007).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from mcp_common.errors import (
    ErrorCode,
    NotPermittedError,
    ToolError,
    map_exception_to_tool_error,
    to_error_envelope,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "docs" / "squad" / "mcp-data-platform" / "api-contract.yaml"


def _fake_exception(module: str, qualname: str, *args: object, **attrs: object) -> Exception:
    cls = type(qualname, (Exception,), {})
    cls.__module__ = module
    exc = cls(*args)
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


# ---------------------------------------------------------------------------
# Contract alignment
# ---------------------------------------------------------------------------


def test_error_code_enum_matches_contract_exactly() -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract_codes = set(spec["components"]["schemas"]["ErrorCode"]["enum"])
    python_codes = {code.value for code in ErrorCode}
    assert python_codes == contract_codes


def test_error_code_disjoint_from_result_status() -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    result_statuses = set(spec["components"]["schemas"]["ResultStatus"]["enum"])
    python_codes = {code.value for code in ErrorCode}
    assert python_codes.isdisjoint(result_statuses)


# ---------------------------------------------------------------------------
# httpx family (real isinstance checks — httpx is a hard dependency)
# ---------------------------------------------------------------------------


def test_httpx_connect_timeout_maps_to_upstream_timeout() -> None:
    exc = httpx.ConnectTimeout("boom")
    error = map_exception_to_tool_error(exc, source="confluence", host="confluence.example.com")
    assert error.code == ErrorCode.UPSTREAM_TIMEOUT
    assert error.retryable is True
    assert "confluence.example.com" in error.details["hint"]


def test_httpx_read_timeout_maps_to_upstream_timeout() -> None:
    error = map_exception_to_tool_error(httpx.ReadTimeout("boom"), source="confluence")
    assert error.code == ErrorCode.UPSTREAM_TIMEOUT


def test_httpx_connect_error_maps_to_upstream_unavailable() -> None:
    error = map_exception_to_tool_error(httpx.ConnectError("dns fail"), source="gitlab")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert error.retryable is True


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_httpx_401_maps_to_unauthorized() -> None:
    error = map_exception_to_tool_error(_status_error(401), source="gitlab")
    assert error.code == ErrorCode.UNAUTHORIZED
    assert error.retryable is False


def test_httpx_403_maps_to_forbidden() -> None:
    error = map_exception_to_tool_error(_status_error(403), source="gitlab")
    assert error.code == ErrorCode.FORBIDDEN


def test_httpx_429_maps_to_rate_limited_with_retry_after() -> None:
    error = map_exception_to_tool_error(
        _status_error(429, headers={"Retry-After": "120"}), source="gitlab"
    )
    assert error.code == ErrorCode.RATE_LIMITED
    assert error.retryable is True
    assert error.retry_after_s == 120


def test_httpx_503_maps_to_upstream_unavailable() -> None:
    error = map_exception_to_tool_error(_status_error(503), source="gitlab")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_httpx_500_maps_to_upstream_error() -> None:
    error = map_exception_to_tool_error(_status_error(500), source="gitlab")
    assert error.code == ErrorCode.UPSTREAM_ERROR


# ---------------------------------------------------------------------------
# Non-HTTP SDK families — matched by (module, qualname), no hard dependency needed
# ---------------------------------------------------------------------------


def test_boto3_client_error_access_denied_maps_to_forbidden() -> None:
    exc = _fake_exception(
        "botocore.exceptions",
        "ClientError",
        "boom",
        response={"Error": {"Code": "AccessDenied"}},
    )
    error = map_exception_to_tool_error(exc, source="cloudwatch")
    assert error.code == ErrorCode.FORBIDDEN


def test_boto3_client_error_throttling_maps_to_rate_limited() -> None:
    exc = _fake_exception(
        "botocore.exceptions",
        "ClientError",
        "boom",
        response={"Error": {"Code": "ThrottlingException"}},
    )
    error = map_exception_to_tool_error(exc, source="cloudwatch")
    assert error.code == ErrorCode.RATE_LIMITED


def test_boto3_endpoint_connection_error_maps_to_upstream_unavailable() -> None:
    exc = _fake_exception("botocore.exceptions", "EndpointConnectionError", "boom")
    error = map_exception_to_tool_error(exc, source="cloudwatch", host="cloudwatch.amazonaws.com")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_boto3_no_credentials_error_maps_to_source_misconfigured() -> None:
    exc = _fake_exception("botocore.exceptions", "NoCredentialsError", "boom")
    error = map_exception_to_tool_error(exc, source="cloudwatch")
    assert error.code == ErrorCode.SOURCE_MISCONFIGURED


def test_psycopg_operational_error_maps_to_upstream_unavailable() -> None:
    exc = _fake_exception("psycopg", "OperationalError", "boom")
    error = map_exception_to_tool_error(exc, source="pgvector")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_psycopg_query_canceled_maps_to_upstream_timeout() -> None:
    exc = _fake_exception("psycopg.errors", "QueryCanceled", "boom")
    error = map_exception_to_tool_error(exc, source="pgvector")
    assert error.code == ErrorCode.UPSTREAM_TIMEOUT


def test_redis_timeout_error_maps_to_upstream_timeout() -> None:
    exc = _fake_exception("redis.exceptions", "TimeoutError", "boom")
    error = map_exception_to_tool_error(exc, source="redis")
    assert error.code == ErrorCode.UPSTREAM_TIMEOUT


def test_redis_connection_error_maps_to_upstream_unavailable() -> None:
    exc = _fake_exception("redis.exceptions", "ConnectionError", "boom")
    error = map_exception_to_tool_error(exc, source="redis")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_redis_no_permission_error_maps_to_forbidden() -> None:
    exc = _fake_exception("redis.exceptions", "NoPermissionError", "boom")
    error = map_exception_to_tool_error(exc, source="redis")
    assert error.code == ErrorCode.FORBIDDEN


def test_kafka_timeout_maps_to_upstream_timeout() -> None:
    exc = _fake_exception("confluent_kafka", "KafkaException", "metadata request timed out")
    error = map_exception_to_tool_error(exc, source="kafka")
    assert error.code == ErrorCode.UPSTREAM_TIMEOUT


def test_kafka_broker_down_maps_to_upstream_unavailable() -> None:
    exc = _fake_exception("confluent_kafka", "KafkaException", "all brokers down")
    error = map_exception_to_tool_error(exc, source="kafka")
    assert error.code == ErrorCode.UPSTREAM_UNAVAILABLE


def test_unknown_exception_maps_to_internal_without_leaking_details() -> None:
    error = map_exception_to_tool_error(ValueError("secret sql: SELECT *"), source="pgvector")
    assert error.code == ErrorCode.INTERNAL
    assert error.retryable is False
    assert "secret sql" not in error.message


# ---------------------------------------------------------------------------
# ToolError / NotPermittedError / envelope rendering
# ---------------------------------------------------------------------------


def test_tool_error_is_an_exception_and_str_includes_code() -> None:
    error = ToolError(
        ErrorCode.INVALID_INPUT, "bad field", "gitlab", False, details={"field": "limit"}
    )
    assert isinstance(error, Exception)
    assert "invalid_input" in str(error)


def test_not_permitted_error_has_fixed_code_and_details() -> None:
    error = NotPermittedError(
        "POST is not allowed", source="opensearch", operation="POST /_search/script",
        allowlist=["GET /_search"],
    )
    assert error.code == ErrorCode.NOT_PERMITTED
    assert error.retryable is False
    assert error.details["operation"] == "POST /_search/script"
    assert error.details["allowlist"] == ["GET /_search"]


def test_map_exception_is_idempotent_on_tool_error() -> None:
    original = NotPermittedError("x", source="redis", operation="SET")
    assert map_exception_to_tool_error(original, source="redis") is original


def test_to_error_envelope_matches_contract_shape() -> None:
    error = ToolError(
        ErrorCode.UPSTREAM_TIMEOUT, "timeout", "confluence", True,
        retry_after_s=None, details={"host": "x"}, request_id="req-1",
    )
    envelope = to_error_envelope(error)
    assert envelope == {
        "status": "error",
        "error": {
            "code": "upstream_timeout",
            "message": "timeout",
            "source": "confluence",
            "retryable": True,
            "retry_after_s": None,
            "details": {"host": "x"},
            "request_id": "req-1",
        },
    }


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_round_trips_through_envelope(code: ErrorCode) -> None:
    error = ToolError(code, "msg", "none", False)
    envelope = to_error_envelope(error)
    assert envelope["error"]["code"] == code.value
