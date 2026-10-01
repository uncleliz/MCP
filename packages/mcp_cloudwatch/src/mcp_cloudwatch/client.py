"""CloudWatch transport layer (ADR-0008): `boto3` behind an API allowlist and a bounded executor.

Read-only guarantees in this file (defense in depth, ADR-0003):

* layer 2a — :meth:`CloudWatchClient.call` checks `service:Operation` against
  :data:`ALLOWED_OPERATIONS` before any I/O;
* layer 2b — the same allowlist is installed as a botocore `before-parameter-build` hook on
  every client, so even code that calls a boto method directly cannot reach a write API;
* layer 3 — the startup check (:meth:`CloudWatchClient.verify_credentials`) asks IAM to
  simulate a set of write actions for our own principal and refuses to serve when any is
  `allowed` (ADR-0003 A1 / ADR-0008 A3).

boto3 is synchronous: every call runs on a bounded `ThreadPoolExecutor` (`max_workers=4`,
ADR-0006 A1) so a hung call can never block the event loop or silently queue later calls (R16).
Timeouts: `connect 3 / read 7 / max_attempts 2` (ADR-0008 A4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore import exceptions as bex
from botocore.config import Config
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError, map_exception_to_tool_error
from mcp_common.readonly import enforce
from mcp_common.runtime import BoundedExecutor

from mcp_cloudwatch.settings import Settings

__all__ = [
    "ALLOWED_OPERATIONS",
    "CloudWatchClient",
    "CredentialReport",
    "SOURCE",
    "principal_arn",
    "to_tool_error",
]

SOURCE = "cloudwatch"
CONNECT_TIMEOUT_S = 3
READ_TIMEOUT_S = 7
# botocore's `max_attempts` counts *retries* (3 tries); ADR-0008 A4 means 2 tries in total,
# so `total_max_attempts` is the setting that keeps 2 * (3 + 7) = 20s below the 25s deadline.
MAX_ATTEMPTS = 2

# service:Operation -> python method name on the boto3 client.
_METHODS: dict[str, str] = {
    "logs:DescribeLogGroups": "describe_log_groups",
    "logs:FilterLogEvents": "filter_log_events",
    "logs:StartQuery": "start_query",
    "logs:GetQueryResults": "get_query_results",
    "logs:StopQuery": "stop_query",
    "cloudwatch:ListMetrics": "list_metrics",
    "cloudwatch:GetMetricData": "get_metric_data",
    "cloudwatch:DescribeAlarms": "describe_alarms",
    "cloudwatch:DescribeAlarmHistory": "describe_alarm_history",
    # Startup credential check only — never exposed as a tool.
    "sts:GetCallerIdentity": "get_caller_identity",
    "iam:SimulatePrincipalPolicy": "simulate_principal_policy",
}
ALLOWED_OPERATIONS: tuple[str, ...] = tuple(_METHODS)

_NOT_FOUND_CODES = frozenset({"ResourceNotFoundException"})
_FORBIDDEN_CODES = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"})
_UNAUTHORIZED_CODES = frozenset(
    {
        "UnrecognizedClientException", "InvalidClientTokenId", "ExpiredToken",
        "ExpiredTokenException", "AuthFailure", "SignatureDoesNotMatch",
    }
)  # fmt: skip
_THROTTLE_CODES = frozenset(
    {"Throttling", "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded",
     "LimitExceededException"}
)  # fmt: skip
_INVALID_CODES = frozenset(
    {"MalformedQueryException", "InvalidParameterException", "InvalidParameterValue",
     "InvalidParameterCombination", "ValidationError", "ValidationException",
     "InvalidNextToken", "InvalidParameterValueException"}
)  # fmt: skip
_UNAVAILABLE_CODES = frozenset({"ServiceUnavailable", "ServiceUnavailableException"})


def to_tool_error(exc: Exception, host: str | None) -> ToolError:
    """Classify a boto3/botocore exception into the contract's error taxonomy."""
    hint = f"kiểm tra VPN/kết nối nội bộ tới {host or 'AWS'}"
    if isinstance(exc, bex.ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        message = str(exc.response.get("Error", {}).get("Message", ""))[:300]
        if code in _NOT_FOUND_CODES:
            return ToolError(
                ErrorCode.UPSTREAM_ERROR, f"AWS {code}: {message}", SOURCE, False,
                details={"upstream_status": 404, "aws_code": code},
            )  # fmt: skip
        if code in _FORBIDDEN_CODES:
            return ToolError(
                ErrorCode.FORBIDDEN, f"AWS {code}: {message}", SOURCE, False,
                details={"aws_code": code},
            )  # fmt: skip
        if code in _UNAUTHORIZED_CODES:
            return ToolError(
                ErrorCode.UNAUTHORIZED, f"AWS {code}: {message}", SOURCE, False,
                details={"aws_code": code},
            )  # fmt: skip
        if code in _THROTTLE_CODES:
            return ToolError(
                ErrorCode.RATE_LIMITED, f"AWS {code}: {message}", SOURCE, True,
                details={"aws_code": code},
            )  # fmt: skip
        if code in _INVALID_CODES:
            return ToolError(
                ErrorCode.INVALID_INPUT, f"query: AWS {code}: {message}", SOURCE, False,
                details={"field": "query", "aws_code": code},
            )  # fmt: skip
        if code in _UNAVAILABLE_CODES or status in (502, 503, 504):
            return ToolError(
                ErrorCode.UPSTREAM_UNAVAILABLE, f"AWS {code or status}", SOURCE, True,
                details={"host": host, "hint": hint, "aws_code": code},
            )  # fmt: skip
        return ToolError(
            ErrorCode.UPSTREAM_ERROR, f"AWS {code}: {message}", SOURCE, True,
            details={"upstream_status": status, "aws_code": code},
        )  # fmt: skip
    if isinstance(exc, bex.ConnectTimeoutError | bex.ReadTimeoutError):
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT, "Timeout khi gọi AWS.", SOURCE, True,
            details={"host": host, "hint": hint},
        )  # fmt: skip
    if isinstance(
        exc, bex.EndpointConnectionError | bex.ConnectionClosedError | bex.HTTPClientError
    ):
        return ToolError(
            ErrorCode.UPSTREAM_UNAVAILABLE, "Không kết nối được tới AWS.", SOURCE, True,
            details={"host": host, "hint": hint},
        )  # fmt: skip
    if isinstance(exc, bex.NoCredentialsError | bex.PartialCredentialsError):
        return ToolError(
            ErrorCode.SOURCE_MISCONFIGURED, "boto3: thiếu hoặc sai credential AWS.", SOURCE, False,
            details={"missing_env": ["MCP_CLOUDWATCH_AWS_ACCESS_KEY_ID",
                                     "MCP_CLOUDWATCH_AWS_SECRET_ACCESS_KEY"]},
        )  # fmt: skip
    return map_exception_to_tool_error(exc, source=SOURCE, host=host)


def principal_arn(caller_arn: str) -> str | None:
    """STS caller ARN -> the IAM ARN `SimulatePrincipalPolicy` accepts (None for root)."""
    parts = caller_arn.split(":")
    if len(parts) < 6:  # noqa: PLR2004
        return None
    partition, service, account, resource = parts[1], parts[2], parts[4], ":".join(parts[5:])
    if service == "sts" and resource.startswith("assumed-role/"):
        role = resource.split("/")[1]
        return f"arn:{partition}:iam::{account}:role/{role}"
    if resource == "root":
        return None
    return caller_arn


@dataclass
class CredentialReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    account: str | None = None


class CloudWatchClient:
    def __init__(
        self,
        settings: Settings,
        *,
        common: CommonSettings | None = None,
        client_factory: Callable[[str], Any] | None = None,
        executor: BoundedExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._common = common or CommonSettings()
        self._host = f"logs.{settings.region}.amazonaws.com"
        self._factory = client_factory or self._default_factory
        self._executor = executor or BoundedExecutor(max_workers=4)
        self._clients: dict[str, Any] = {}

    def _default_factory(self, service: str) -> Any:
        settings = self._settings
        keys: dict[str, str] = {}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            keys = {
                "aws_access_key_id": settings.aws_access_key_id.get_secret_value(),
                "aws_secret_access_key": settings.aws_secret_access_key.get_secret_value(),
            }
        return boto3.client(
            service,
            region_name=settings.region,
            endpoint_url=settings.endpoint_url,
            config=Config(
                connect_timeout=CONNECT_TIMEOUT_S,
                read_timeout=READ_TIMEOUT_S,
                retries={"total_max_attempts": MAX_ATTEMPTS, "mode": "standard"},
                user_agent_extra="mcp-data-platform/cloudwatch-readonly",
            ),
            **keys,
        )

    def _boto(self, service: str) -> Any:
        if service not in self._clients:
            client = self._factory(service)

            def guard(model: Any, **_kwargs: Any) -> None:
                enforce(
                    ALLOWED_OPERATIONS,
                    f"{model.service_model.service_name}:{model.name}",
                    source=SOURCE,
                )

            # `first`: runs ahead of the other hooks so a write API never even validates.
            client.meta.events.register_first("before-parameter-build.*.*", guard)
            self._clients[service] = client
        return self._clients[service]

    async def aclose(self) -> None:
        self._executor.shutdown(wait=False)

    async def call(self, service: str, operation: str, **params: Any) -> Any:
        """The single choke point: allowlist -> bounded executor -> boto3."""
        key = f"{service}:{operation}"
        enforce(ALLOWED_OPERATIONS, key, source=SOURCE)
        method = getattr(self._boto(service), _METHODS[key])
        try:
            return await self._executor.run(method, source=SOURCE, host=self._host, **params)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to the contract taxonomy
            raise to_tool_error(exc, self._host) from exc

    # -- startup credential check (ADR-0003 A1, ADR-0008 A3) -------------------------------------

    async def verify_credentials(self) -> CredentialReport:
        """`sts:GetCallerIdentity`, then `iam:SimulatePrincipalPolicy` for write actions."""
        try:
            identity = await self.call("sts", "GetCallerIdentity")
        except ToolError as exc:
            return CredentialReport(False, [f"{exc.code.value}: {exc.message}"])
        account = str(identity.get("Account") or "") or None
        principal = principal_arn(str(identity.get("Arn") or ""))
        if principal is None:
            return CredentialReport(
                True, [], ["root/unknown principal cannot be simulated; read-only is unverified"],
                account,
            )  # fmt: skip
        actions = self._settings.simulate_action_list
        try:
            simulated = await self.call(
                "iam", "SimulatePrincipalPolicy", PolicySourceArn=principal, ActionNames=actions
            )
        except ToolError as exc:
            return CredentialReport(
                True, [],
                [f"iam:SimulatePrincipalPolicy not available ({exc.code.value}); the IAM policy "
                 "must be verified by the operator (grant it to enable the startup assert)"],
                account,
            )  # fmt: skip
        allowed = sorted(
            r["EvalActionName"]
            for r in simulated.get("EvaluationResults", [])
            if r.get("EvalDecision") == "allowed"
        )
        if allowed:
            return CredentialReport(
                False,
                [
                    f"principal {principal} is allowed write actions: {', '.join(allowed)} "
                    "(attach a read-only IAM policy)"
                ],
                [],
                account,
            )
        return CredentialReport(True, [], [], account)

    async def credential_check(self) -> bool:
        """`mcp_common.runtime.serve(credential_check=...)` hook."""
        return (await self.verify_credentials()).ok
