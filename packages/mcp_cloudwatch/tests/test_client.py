"""T-041: allowlist, bounded executor, error mapping and IAM startup check of the client."""

from __future__ import annotations

import asyncio
import threading

import pytest
from botocore import exceptions as bex
from cw_helpers import GROUP, Stubs
from mcp_cloudwatch.client import (
    ALLOWED_OPERATIONS,
    CloudWatchClient,
    principal_arn,
    to_tool_error,
)
from mcp_cloudwatch.settings import Settings
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.runtime import BoundedExecutor

WRITE_ACTIONS = [
    ("logs", "PutLogEvents"), ("logs", "DeleteLogGroup"), ("logs", "CreateLogGroup"),
    ("logs", "PutRetentionPolicy"), ("logs", "PutResourcePolicy"), ("logs", "DeleteLogStream"),
    ("cloudwatch", "PutMetricData"), ("cloudwatch", "DeleteAlarms"),
    ("cloudwatch", "PutMetricAlarm"), ("cloudwatch", "SetAlarmState"),
    ("cloudwatch", "DisableAlarmActions"), ("iam", "CreateUser"), ("sts", "AssumeRoleWithSAML"),
]  # fmt: skip


def test_FR_006_allowlist_has_only_describe_get_list_filter_and_insights_calls() -> None:
    for op in ALLOWED_OPERATIONS:
        _service, _, name = op.partition(":")
        assert name.startswith(("Describe", "Get", "List", "Filter", "Simulate")) or name in {
            "StartQuery",
            "StopQuery",
        }, op


@pytest.mark.parametrize(("service", "operation"), WRITE_ACTIONS)
@pytest.mark.asyncio
async def test_FR_014_AC_001_write_apis_are_not_permitted_before_any_call(
    client: CloudWatchClient, stubs: Stubs, service: str, operation: str
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.call(service, operation)
    assert exc.value.details["operation"] == f"{service}:{operation}"
    assert exc.value.details["allowlist"] == list(ALLOWED_OPERATIONS)


@pytest.mark.asyncio
async def test_botocore_level_guard_blocks_a_write_even_if_the_boto_method_is_called_directly(
    settings: Settings, common: CommonSettings
) -> None:
    c = CloudWatchClient(settings, common=common)  # real boto3 clients, no stubber, no network
    with pytest.raises(NotPermittedError) as exc:
        c._boto("logs").delete_log_group(logGroupName=GROUP)  # bypassing client.call()
    assert exc.value.details["operation"] == "logs:DeleteLogGroup"
    with pytest.raises(NotPermittedError):
        c._boto("cloudwatch").put_metric_data(Namespace="x", MetricData=[])


@pytest.mark.asyncio
async def test_call_runs_the_sdk_on_the_bounded_executor_thread(
    settings: Settings, common: CommonSettings, stubs: Stubs
) -> None:
    seen: list[str] = []
    executor = BoundedExecutor(max_workers=2)
    original = executor.run

    async def spying_run(func, *args, **kwargs):
        def wrapper(*a, **k):
            seen.append(threading.current_thread().name)
            return func(*a, **k)

        return await original(wrapper, *args, **kwargs)

    executor.run = spying_run  # type: ignore[method-assign]
    c = CloudWatchClient(settings, common=common, client_factory=stubs.factory(), executor=executor)
    stubs.add("logs", "describe_log_groups", {"logGroups": []}, {"limit": 5})
    assert await c.call("logs", "DescribeLogGroups", limit=5) == {"logGroups": []}
    assert seen and seen[0] != threading.current_thread().name
    assert seen[0].startswith("ThreadPoolExecutor")
    executor.shutdown()


@pytest.mark.asyncio
async def test_R16_executor_exhausted_by_hung_calls_fails_the_next_call_fast(
    settings: Settings, common: CommonSettings, stubs: Stubs
) -> None:
    release = threading.Event()
    executor = BoundedExecutor(max_workers=2)
    c = CloudWatchClient(settings, common=common, client_factory=stubs.factory(), executor=executor)

    class Hung:
        meta = type(
            "M", (), {"events": type("E", (), {"register_first": lambda *a, **k: None})()}
        )()

        def describe_log_groups(self, **_kwargs):
            release.wait(10)
            return {"logGroups": []}

    c._clients["logs"] = Hung()
    hung = [asyncio.create_task(c.call("logs", "DescribeLogGroups")) for _ in range(2)]
    await asyncio.sleep(0.1)
    started = asyncio.get_running_loop().time()
    with pytest.raises(ToolError) as exc:
        await c.call("logs", "DescribeLogGroups")
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert asyncio.get_running_loop().time() - started < 0.5  # immediate, never queued
    release.set()
    await asyncio.gather(*hung)
    executor.shutdown()


def test_default_boto_clients_use_documented_timeouts_and_attempts(
    settings: Settings, common: CommonSettings
) -> None:
    c = CloudWatchClient(settings, common=common)
    config = c._boto("logs").meta.config
    assert config.connect_timeout == 3 and config.read_timeout == 7
    assert config.retries["total_max_attempts"] == 2 and config.retries["mode"] == "standard"
    assert c._boto("logs") is c._boto("logs")  # cached per service
    assert c._boto("logs").meta.region_name == settings.region


def test_endpoint_url_override_for_local_emulators(common: CommonSettings) -> None:
    c = CloudWatchClient(
        Settings(region="ap-southeast-1", endpoint_url="http://localhost:4566"), common=common
    )
    assert c._boto("logs").meta.endpoint_url == "http://localhost:4566"


# -- error mapping --------------------------------------------------------------------------------


def _client_error(code: str, status: int = 400) -> bex.ClientError:
    return bex.ClientError(
        {"Error": {"Code": code, "Message": "m"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "Op",
    )


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_client_error("ResourceNotFoundException", 400), ErrorCode.UPSTREAM_ERROR),
        (_client_error("AccessDeniedException", 403), ErrorCode.FORBIDDEN),
        (_client_error("ExpiredTokenException"), ErrorCode.UNAUTHORIZED),
        (_client_error("UnrecognizedClientException", 403), ErrorCode.UNAUTHORIZED),
        (_client_error("ThrottlingException", 400), ErrorCode.RATE_LIMITED),
        (_client_error("MalformedQueryException"), ErrorCode.INVALID_INPUT),
        (_client_error("InvalidParameterException"), ErrorCode.INVALID_INPUT),
        (_client_error("ServiceUnavailableException", 503), ErrorCode.UPSTREAM_UNAVAILABLE),
        (_client_error("InternalFailure", 500), ErrorCode.UPSTREAM_ERROR),
        (bex.ConnectTimeoutError(endpoint_url="https://logs"), ErrorCode.UPSTREAM_TIMEOUT),
        (bex.ReadTimeoutError(endpoint_url="https://logs"), ErrorCode.UPSTREAM_TIMEOUT),
        (bex.EndpointConnectionError(endpoint_url="https://logs"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (bex.ConnectionClosedError(endpoint_url="https://logs"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (bex.NoCredentialsError(), ErrorCode.SOURCE_MISCONFIGURED),
        (RuntimeError("x"), ErrorCode.INTERNAL),
    ],
)
def test_sdk_exceptions_map_to_contract_error_codes(exc, code) -> None:
    error = to_tool_error(exc, host="logs.ap-southeast-1.amazonaws.com")
    assert error.code == code and error.source == "cloudwatch"
    if code in {ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE}:
        assert "VPN" in error.details["hint"]


def test_resource_not_found_is_flagged_for_not_found_detection() -> None:
    error = to_tool_error(_client_error("ResourceNotFoundException"), host="h")
    assert error.details["upstream_status"] == 404 and error.details["aws_code"] == (
        "ResourceNotFoundException"
    )
    assert to_tool_error(_client_error("MalformedQueryException"), "h").details["field"] == "query"


@pytest.mark.asyncio
async def test_sdk_errors_become_tool_errors_in_the_wrapper(
    client: CloudWatchClient, stubs: Stubs
) -> None:
    stubs.error("logs", "describe_log_groups", "AccessDeniedException", status=403)
    with pytest.raises(ToolError) as exc:
        await client.call("logs", "DescribeLogGroups")
    assert exc.value.code == ErrorCode.FORBIDDEN


# -- startup check (ADR-0003 A1, ADR-0008 A3) -----------------------------------------------------

IDENTITY = {
    "UserId": "AROAEXAMPLE:sess",
    "Account": "123456789012",
    "Arn": "arn:aws:sts::123456789012:assumed-role/mcp-readonly/sess",
}


def _simulate(decisions: dict[str, str]) -> dict:
    return {
        "EvaluationResults": [
            {"EvalActionName": action, "EvalDecision": decision, "EvalResourceName": "*"}
            for action, decision in decisions.items()
        ]
    }


def test_principal_arn_normalises_assumed_roles() -> None:
    assert principal_arn(IDENTITY["Arn"]) == "arn:aws:iam::123456789012:role/mcp-readonly"
    user = "arn:aws:iam::123456789012:user/alice"
    assert principal_arn(user) == user
    assert principal_arn("arn:aws:iam::123456789012:root") is None
    assert principal_arn("arn:aws-cn:sts::1:assumed-role/r/s") == "arn:aws-cn:iam::1:role/r"


@pytest.mark.asyncio
async def test_FR_006_startup_check_green_when_every_write_action_is_implicit_deny(
    client: CloudWatchClient, stubs: Stubs, settings: Settings
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    actions = settings.simulate_action_list
    stubs.add(
        "iam", "simulate_principal_policy",
        _simulate({a: "implicitDeny" for a in actions}),
        {"PolicySourceArn": "arn:aws:iam::123456789012:role/mcp-readonly", "ActionNames": actions},
    )  # fmt: skip
    report = await client.verify_credentials()
    assert report.ok and report.reasons == [] and report.account == "123456789012"
    stubs.assert_all_consumed()


@pytest.mark.asyncio
async def test_TC_ADR_0008_A3_simulate_allowed_for_a_write_action_refuses_to_serve(
    client: CloudWatchClient, stubs: Stubs, settings: Settings
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    decisions = {a: "implicitDeny" for a in settings.simulate_action_list}
    decisions["logs:DeleteLogGroup"] = "allowed"
    stubs.add("iam", "simulate_principal_policy", _simulate(decisions))
    report = await client.verify_credentials()
    assert not report.ok and "logs:DeleteLogGroup" in report.reasons[0]
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate(decisions))
    assert (await client.credential_check()) is False


@pytest.mark.asyncio
async def test_startup_check_explicit_deny_counts_as_safe(
    client: CloudWatchClient, stubs: Stubs, settings: Settings
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add(
        "iam", "simulate_principal_policy",
        _simulate({a: "explicitDeny" for a in settings.simulate_action_list}),
    )  # fmt: skip
    assert (await client.verify_credentials()).ok


@pytest.mark.asyncio
async def test_startup_check_simulate_not_permitted_is_a_warning_not_a_failure(
    client: CloudWatchClient, stubs: Stubs
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.error("iam", "simulate_principal_policy", "AccessDenied", status=403)
    report = await client.verify_credentials()
    assert report.ok and report.warnings and "SimulatePrincipalPolicy" in report.warnings[0]


@pytest.mark.asyncio
async def test_startup_check_root_principal_cannot_be_simulated(
    client: CloudWatchClient, stubs: Stubs
) -> None:
    stubs.add("sts", "get_caller_identity", {**IDENTITY, "Arn": "arn:aws:iam::123456789012:root"})
    report = await client.verify_credentials()
    assert report.ok and any("root" in w for w in report.warnings)


@pytest.mark.asyncio
async def test_startup_check_fails_without_valid_credentials(
    client: CloudWatchClient, stubs: Stubs
) -> None:
    stubs.error("sts", "get_caller_identity", "InvalidClientTokenId", status=403)
    report = await client.verify_credentials()
    assert not report.ok and "unauthorized" in report.reasons[0]
