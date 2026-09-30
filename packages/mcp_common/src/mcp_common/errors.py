"""T-007: the `ErrorCode` taxonomy + a mapper from source-SDK exceptions to it.

Mirrors `components.schemas.{ErrorCode,Error,ErrorEnvelope}` in `api-contract.yaml`
(ADR-0004). This module does **not** decide retry policy — that is `mcp_common.http`'s
job (ADR-0006); it only classifies an already-failed call into the fixed taxonomy.

Exceptions from boto3/psycopg/redis/confluent-kafka are recognised by fully-qualified
class name (module + qualname), **not** by `isinstance`, on purpose: `mcp_common` is a
dependency of every one of the 10 packages (ADR-0001), most of which never touch
AWS/Postgres/Redis/Kafka, so it must not force those heavy SDKs onto every server just
to classify an error. `httpx` is the one exception — it is already a hard dependency
via `mcp_common.http`, so those exceptions are matched with real `isinstance` checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

__all__ = [
    "ErrorCode",
    "ToolError",
    "NotPermittedError",
    "to_error_envelope",
    "map_exception_to_tool_error",
]


class ErrorCode(StrEnum):
    """Matches `components.schemas.ErrorCode` in api-contract.yaml exactly.

    `empty`/`not_found` are deliberately absent — they are `ResultStatus` values of a
    *successful* result (ADR-0004), never an `ErrorCode`.
    """

    INVALID_INPUT = "invalid_input"
    NOT_PERMITTED = "not_permitted"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_ERROR = "upstream_error"
    RATE_LIMITED = "rate_limited"
    RESPONSE_TOO_LARGE = "response_too_large"
    SOURCE_MISCONFIGURED = "source_misconfigured"
    INTERNAL = "internal"


@dataclass
class ToolError(Exception):
    """Mirrors `components.schemas.Error`. Raised by tool code, caught by
    `mcp_common.runtime` and turned into an `ErrorEnvelope` (T-012)."""

    code: ErrorCode
    message: str
    source: str
    retryable: bool
    retry_after_s: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code.value}: {self.message}"


class NotPermittedError(ToolError):
    """Read-only guard rejection (FR-014, ADR-0003). Always `retryable=False`."""

    def __init__(
        self,
        message: str,
        *,
        source: str,
        operation: str,
        allowlist: Sequence[str] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.NOT_PERMITTED,
            message=message,
            source=source,
            retryable=False,
            details={"operation": operation, "allowlist": list(allowlist or [])},
            request_id=request_id,
        )


def to_error_envelope(error: ToolError) -> dict[str, Any]:
    """Render a `ToolError` as the `ErrorEnvelope` payload the contract specifies."""
    return {
        "status": "error",
        "error": {
            "code": error.code.value,
            "message": error.message,
            "source": error.source,
            "retryable": error.retryable,
            "retry_after_s": error.retry_after_s,
            "details": error.details,
            "request_id": error.request_id,
        },
    }


def _vpn_hint(host: str | None) -> str:
    target = host or "nguồn"
    return f"kiểm tra VPN/kết nối nội bộ tới {target}"


def _from_httpx(exc: Exception, *, source: str, host: str | None) -> ToolError | None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return ToolError(
                ErrorCode.UNAUTHORIZED, "Upstream trả 401.", source, False,
                details={"upstream_status": status},
            )
        if status == 403:
            return ToolError(
                ErrorCode.FORBIDDEN, "Upstream trả 403.", source, False,
                details={"upstream_status": status},
            )
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After")
            return ToolError(
                ErrorCode.RATE_LIMITED,
                "Upstream trả 429 (rate limited).",
                source,
                True,
                retry_after_s=int(retry_after) if retry_after and retry_after.isdigit() else None,
                details={"upstream_status": status},
            )
        if status in (502, 503, 504):
            return ToolError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"Upstream trả {status}.",
                source,
                True,
                details={"upstream_status": status, "host": host, "hint": _vpn_hint(host)},
            )
        return ToolError(
            ErrorCode.UPSTREAM_ERROR, f"Upstream trả {status}.", source, True,
            details={"upstream_status": status},
        )
    if isinstance(exc, httpx.TimeoutException):
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT,
            "Timeout khi gọi upstream.",
            source,
            True,
            details={"host": host, "hint": _vpn_hint(host)},
        )
    if isinstance(exc, httpx.ConnectError | httpx.NetworkError):
        return ToolError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "Không kết nối được tới upstream.",
            source,
            True,
            details={"host": host, "hint": _vpn_hint(host)},
        )
    return None


def _aws_client_error_code(exc: Exception) -> ErrorCode:
    response = getattr(exc, "response", None) or {}
    aws_code = str((response.get("Error") or {}).get("Code", ""))
    if aws_code in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
    }:
        return ErrorCode.FORBIDDEN
    if aws_code in {
        "UnrecognizedClientException",
        "InvalidClientTokenId",
        "ExpiredToken",
        "AuthFailure",
    }:
        return ErrorCode.UNAUTHORIZED
    if aws_code in {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
        "RequestLimitExceeded",
    }:
        return ErrorCode.RATE_LIMITED
    return ErrorCode.UPSTREAM_ERROR


# (module, qualname) -> builder(exc, source, host) -> ToolError
_NON_HTTPX_SDK_RULES: dict[tuple[str, str], Any] = {
    ("botocore.exceptions", "ClientError"): lambda exc, source, host: ToolError(
        _aws_client_error_code(exc), f"boto3 ClientError: {exc}", source,
        _aws_client_error_code(exc) in {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_ERROR},
        details={"host": host},
    ),
    ("botocore.exceptions", "EndpointConnectionError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_UNAVAILABLE, "boto3 không kết nối được endpoint.", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("botocore.exceptions", "ConnectTimeoutError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_TIMEOUT, "boto3 connect timeout.", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("botocore.exceptions", "ReadTimeoutError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_TIMEOUT, "boto3 read timeout.", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("botocore.exceptions", "NoCredentialsError"): lambda exc, source, host: ToolError(
        ErrorCode.SOURCE_MISCONFIGURED, "boto3: thiếu credential.", source, False,
        details={"missing_env": "AWS credentials"},
    ),
    ("psycopg", "OperationalError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_UNAVAILABLE, f"Postgres operational error: {exc}", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("psycopg.errors", "QueryCanceled"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_TIMEOUT, "Postgres statement_timeout hit.", source, True,
        details={"host": host},
    ),
    ("psycopg.errors", "InsufficientPrivilege"): lambda exc, source, host: ToolError(
        ErrorCode.FORBIDDEN, "Postgres: thiếu quyền (role read-only).", source, False,
        details={},
    ),
    ("redis.exceptions", "TimeoutError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_TIMEOUT, "Redis timeout.", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("redis.exceptions", "ConnectionError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_UNAVAILABLE, "Redis không kết nối được.", source, True,
        details={"host": host, "hint": _vpn_hint(host)},
    ),
    ("redis.exceptions", "AuthenticationError"): lambda exc, source, host: ToolError(
        ErrorCode.UNAUTHORIZED, "Redis: sai credential.", source, False, details={},
    ),
    ("redis.exceptions", "NoPermissionError"): lambda exc, source, host: ToolError(
        ErrorCode.FORBIDDEN, "Redis: ACL user thiếu quyền.", source, False, details={},
    ),
    ("redis.exceptions", "ResponseError"): lambda exc, source, host: ToolError(
        ErrorCode.UPSTREAM_ERROR, f"Redis response error: {exc}", source, True, details={},
    ),
}


def _from_kafka(exc: Exception, *, source: str, host: str | None) -> ToolError | None:
    module = type(exc).__module__
    if not module.startswith("confluent_kafka") and not module.startswith("kafka"):
        return None
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT, f"Kafka timeout: {exc}", source, True,
            details={"host": host, "hint": _vpn_hint(host)},
        )
    if "connection refused" in text or "transport failure" in text or "all brokers down" in text:
        return ToolError(
            ErrorCode.UPSTREAM_UNAVAILABLE, f"Kafka broker unavailable: {exc}", source, True,
            details={"host": host, "hint": _vpn_hint(host)},
        )
    if "authentication" in text or "sasl" in text:
        return ToolError(
            ErrorCode.UNAUTHORIZED,
            f"Kafka authentication failed: {exc}",
            source,
            False,
            details={},
        )
    return ToolError(ErrorCode.UPSTREAM_ERROR, f"Kafka error: {exc}", source, True, details={})


def map_exception_to_tool_error(
    exc: Exception, *, source: str, host: str | None = None
) -> ToolError:
    """Classify an arbitrary SDK exception into the fixed `ErrorCode` taxonomy.

    Falls back to `ErrorCode.INTERNAL` for anything unrecognised — per architecture.md's
    Error model, `internal` never leaks a stack trace/secret, only a generic message.
    """
    if isinstance(exc, ToolError):
        return exc

    from_httpx = _from_httpx(exc, source=source, host=host)
    if from_httpx is not None:
        return from_httpx

    from_kafka = _from_kafka(exc, source=source, host=host)
    if from_kafka is not None:
        return from_kafka

    key = (type(exc).__module__, type(exc).__qualname__)
    builder = _NON_HTTPX_SDK_RULES.get(key)
    if builder is not None:
        return builder(exc, source, host)

    return ToolError(
        ErrorCode.INTERNAL,
        "Lỗi nội bộ không mong đợi.",
        source,
        False,
        details={},
    )
