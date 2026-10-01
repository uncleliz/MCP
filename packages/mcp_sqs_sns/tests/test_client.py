"""T-059 / TC-037: allowlist, bounded executor, error mapping and IAM startup check."""

from __future__ import annotations

import asyncio
import threading

import pytest
from botocore import exceptions as bex
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.runtime import BoundedExecutor
from mcp_sqs_sns.client import (
    ALLOWED_OPERATIONS,
    SqsSnsClient,
    principal_arn,
    source_for,
    to_tool_error,
)
from mcp_sqs_sns.settings import Settings
from sqs_helpers import QUEUE_URL, TOPIC_ARN, Stubs

# Everything that writes or has a side effect — including ReceiveMessage (visibility timeout).
FORBIDDEN = [
    ("sqs", "ReceiveMessage"), ("sqs", "SendMessage"), ("sqs", "SendMessageBatch"),
    ("sqs", "DeleteMessage"), ("sqs", "DeleteMessageBatch"), ("sqs", "PurgeQueue"),
    ("sqs", "DeleteQueue"), ("sqs", "CreateQueue"), ("sqs", "SetQueueAttributes"),
    ("sqs", "ChangeMessageVisibility"), ("sqs", "TagQueue"), ("sqs", "AddPermission"),
    ("sns", "Publish"), ("sns", "PublishBatch"), ("sns", "CreateTopic"), ("sns", "DeleteTopic"),
    ("sns", "Subscribe"), ("sns", "Unsubscribe"), ("sns", "SetTopicAttributes"),
    ("iam", "CreateUser"), ("sts", "AssumeRole"),
]  # fmt: skip


def test_FR_010_AC_003_allowlist_is_exactly_the_metadata_reads() -> None:
    assert set(ALLOWED_OPERATIONS) == {
        "sqs:ListQueues", "sqs:GetQueueUrl", "sqs:GetQueueAttributes", "sqs:ListQueueTags",
        "sqs:ListDeadLetterSourceQueues", "sns:ListTopics", "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic", "sts:GetCallerIdentity", "iam:SimulatePrincipalPolicy",
    }  # fmt: skip
    for op in ALLOWED_OPERATIONS:
        name = op.partition(":")[2]
        assert name.startswith(("List", "Get", "Simulate")), op


@pytest.mark.parametrize(("service", "operation"), FORBIDDEN)
@pytest.mark.asyncio
async def test_TC_037_FR_010_AC_003_write_and_receive_apis_are_not_permitted_before_any_call(
    client: SqsSnsClient, stubs: Stubs, service: str, operation: str
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.call(service, operation)
    assert exc.value.details["operation"] == f"{service}:{operation}"
    assert exc.value.details["allowlist"] == list(ALLOWED_OPERATIONS)
    assert exc.value.code == ErrorCode.NOT_PERMITTED
    assert exc.value.source == source_for(service)
    stubs.assert_all_consumed()  # nothing was sent


@pytest.mark.asyncio
async def test_botocore_level_guard_blocks_receive_and_send_even_if_called_directly(
    settings: Settings, common: CommonSettings
) -> None:
    c = SqsSnsClient(settings, common=common)  # real boto3 clients, no stubber, no network
    with pytest.raises(NotPermittedError) as exc:
        c._boto("sqs").receive_message(QueueUrl=QUEUE_URL)  # bypassing client.call()
    assert exc.value.details["operation"] == "sqs:ReceiveMessage"
    with pytest.raises(NotPermittedError):
        c._boto("sqs").send_message(QueueUrl=QUEUE_URL, MessageBody="x")
    with pytest.raises(NotPermittedError):
        c._boto("sqs").delete_message(QueueUrl=QUEUE_URL, ReceiptHandle="r")
    with pytest.raises(NotPermittedError) as pub:
        c._boto("sns").publish(TopicArn=TOPIC_ARN, Message="x")
    assert pub.value.source == "sns"


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
    c = SqsSnsClient(settings, common=common, client_factory=stubs.factory(), executor=executor)
    stubs.add("sqs", "list_queues", {"QueueUrls": []}, {"MaxResults": 5})
    assert await c.call("sqs", "ListQueues", MaxResults=5) == {"QueueUrls": []}
    assert seen and seen[0] != threading.current_thread().name
    await c.aclose()


@pytest.mark.asyncio
async def test_R16_executor_exhausted_by_hung_calls_fails_the_next_call_fast(
    settings: Settings, common: CommonSettings, stubs: Stubs
) -> None:
    release = threading.Event()
    executor = BoundedExecutor(max_workers=2)
    c = SqsSnsClient(settings, common=common, client_factory=stubs.factory(), executor=executor)

    class Hung:
        meta = type(
            "M", (), {"events": type("E", (), {"register_first": lambda *a, **k: None})()}
        )()

        def list_queues(self, **_kwargs):
            release.wait(10)
            return {"QueueUrls": []}

    c._clients["sqs"] = Hung()
    hung = [asyncio.create_task(c.call("sqs", "ListQueues")) for _ in range(2)]
    await asyncio.sleep(0.1)
    started = asyncio.get_running_loop().time()
    with pytest.raises(ToolError) as exc:
        await c.call("sqs", "ListQueues")
    assert exc.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert asyncio.get_running_loop().time() - started < 0.5
    release.set()
    await asyncio.gather(*hung)
    executor.shutdown()


def test_default_boto_clients_use_documented_timeouts_and_attempts(
    settings: Settings, common: CommonSettings
) -> None:
    c = SqsSnsClient(settings, common=common)
    config = c._boto("sqs").meta.config
    assert config.connect_timeout == 3 and config.read_timeout == 7
    assert config.retries["total_max_attempts"] == 2
    assert c._boto("sqs") is c._boto("sqs")
    assert c.region == settings.region


def test_default_clients_work_with_the_credential_chain_and_an_endpoint_override(
    common: CommonSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLEEXAMPLEXX")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "chain-secret")
    c = SqsSnsClient(
        Settings(region="ap-southeast-1", endpoint_url="http://localhost:4566"), common=common
    )
    assert c._boto("sqs").meta.endpoint_url == "http://localhost:4566"
    assert c._boto("sns").meta.endpoint_url == "http://localhost:4566"


# -- error mapping -------------------------------------------------------------------------------


def _client_error(code: str, status: int = 400) -> bex.ClientError:
    return bex.ClientError(
        {"Error": {"Code": code, "Message": "m"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "Op",
    )


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_client_error("AWS.SimpleQueueService.NonExistentQueue"), ErrorCode.UPSTREAM_ERROR),
        (_client_error("QueueDoesNotExist"), ErrorCode.UPSTREAM_ERROR),
        (_client_error("NotFound", 404), ErrorCode.UPSTREAM_ERROR),
        (_client_error("AccessDenied", 403), ErrorCode.FORBIDDEN),
        (_client_error("AuthorizationError", 403), ErrorCode.FORBIDDEN),
        (_client_error("ExpiredToken"), ErrorCode.UNAUTHORIZED),
        (_client_error("InvalidClientTokenId", 403), ErrorCode.UNAUTHORIZED),
        (_client_error("Throttling"), ErrorCode.RATE_LIMITED),
        (_client_error("RequestThrottled"), ErrorCode.RATE_LIMITED),
        (_client_error("InvalidParameter"), ErrorCode.INVALID_INPUT),
        (_client_error("InvalidParameterValue"), ErrorCode.INVALID_INPUT),
        (_client_error("ServiceUnavailable", 503), ErrorCode.UPSTREAM_UNAVAILABLE),
        (_client_error("Whatever", 502), ErrorCode.UPSTREAM_UNAVAILABLE),
        (_client_error("Mystery", 500), ErrorCode.UPSTREAM_ERROR),
        (bex.ConnectTimeoutError(endpoint_url="https://sqs"), ErrorCode.UPSTREAM_TIMEOUT),
        (bex.ReadTimeoutError(endpoint_url="https://sqs"), ErrorCode.UPSTREAM_TIMEOUT),
        (bex.EndpointConnectionError(endpoint_url="https://sqs"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (bex.ConnectionClosedError(endpoint_url="https://sqs"), ErrorCode.UPSTREAM_UNAVAILABLE),
        (bex.NoCredentialsError(), ErrorCode.SOURCE_MISCONFIGURED),
        (RuntimeError("x"), ErrorCode.INTERNAL),
    ],
)
def test_sdk_exceptions_map_to_contract_error_codes(exc, code) -> None:
    error = to_tool_error(exc, host="sqs.ap-southeast-1.amazonaws.com", source="sqs")
    assert error.code == code and error.source in {"sqs", "none"}
    if code in {ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE}:
        assert "VPN" in error.details["hint"]


def test_missing_queue_and_topic_are_flagged_for_not_found_detection() -> None:
    for code in ("AWS.SimpleQueueService.NonExistentQueue", "NotFound"):
        error = to_tool_error(_client_error(code), host="h")
        assert error.details["upstream_status"] == 404 and error.details["aws_code"] == code


def test_missing_credentials_error_names_the_env_vars() -> None:
    error = to_tool_error(bex.NoCredentialsError(), host=None)
    assert "MCP_SQS_SNS_AWS_ACCESS_KEY_ID" in error.details["missing_env"]


@pytest.mark.asyncio
async def test_sdk_errors_become_tool_errors_with_the_service_as_source(
    client: SqsSnsClient, stubs: Stubs
) -> None:
    stubs.error("sns", "list_topics", "AuthorizationError", status=403)
    with pytest.raises(ToolError) as exc:
        await client.call("sns", "ListTopics")
    assert exc.value.code == ErrorCode.FORBIDDEN and exc.value.source == "sns"


# -- startup check (ADR-0003 A1, ADR-0008 A3) ----------------------------------------------------

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


def _all(settings: Settings, decision: str = "implicitDeny") -> dict[str, str]:
    return {a: decision for a in [*settings.simulate_action_list, "sqs:ReceiveMessage"]}


def test_principal_arn_normalises_assumed_roles() -> None:
    assert principal_arn(IDENTITY["Arn"]) == "arn:aws:iam::123456789012:role/mcp-readonly"
    user = "arn:aws:iam::123456789012:user/alice"
    assert principal_arn(user) == user
    assert principal_arn("arn:aws:iam::123456789012:root") is None
    assert principal_arn("short") is None


def test_the_simulated_write_actions_cover_send_delete_publish_and_purge() -> None:
    actions = Settings(region="r").simulate_action_list
    for expected in ("sqs:SendMessage", "sqs:DeleteMessage", "sqs:PurgeQueue", "sns:Publish"):
        assert expected in actions
    assert "sqs:ReceiveMessage" not in actions  # handled as a warning, see below


@pytest.mark.asyncio
async def test_FR_010_AC_003_startup_check_green_when_every_write_action_is_denied(
    client: SqsSnsClient, stubs: Stubs, settings: Settings
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add(
        "iam", "simulate_principal_policy", _simulate(_all(settings)),
        {
            "PolicySourceArn": "arn:aws:iam::123456789012:role/mcp-readonly",
            "ActionNames": [*settings.simulate_action_list, "sqs:ReceiveMessage"],
        },
    )  # fmt: skip
    report = await client.verify_credentials()
    assert report.ok and report.reasons == [] and report.warnings == []
    assert report.account == "123456789012"
    stubs.assert_all_consumed()


@pytest.mark.parametrize("action", ["sqs:SendMessage", "sqs:DeleteMessage", "sns:Publish"])
@pytest.mark.asyncio
async def test_FR_014_AC_001_simulate_allowed_for_a_write_action_refuses_to_serve(
    client: SqsSnsClient, stubs: Stubs, settings: Settings, action: str
) -> None:
    decisions = _all(settings)
    decisions[action] = "allowed"
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate(decisions))
    report = await client.verify_credentials()
    assert not report.ok and action in report.reasons[0]
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate(decisions))
    assert (await client.credential_check()) is False


@pytest.mark.asyncio
async def test_receive_message_in_the_policy_is_a_warning_not_a_refusal(
    client: SqsSnsClient, stubs: Stubs, settings: Settings
) -> None:
    decisions = _all(settings)
    decisions["sqs:ReceiveMessage"] = "allowed"  # e.g. AWS's AmazonSQSReadOnlyAccess
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.add("iam", "simulate_principal_policy", _simulate(decisions))
    report = await client.verify_credentials()
    assert report.ok and report.reasons == []
    assert "sqs:ReceiveMessage" in report.warnings[0]


@pytest.mark.asyncio
async def test_startup_check_simulate_not_permitted_is_a_warning_not_a_failure(
    client: SqsSnsClient, stubs: Stubs
) -> None:
    stubs.add("sts", "get_caller_identity", IDENTITY)
    stubs.error("iam", "simulate_principal_policy", "AccessDenied", status=403)
    report = await client.verify_credentials()
    assert report.ok and "SimulatePrincipalPolicy" in report.warnings[0]


@pytest.mark.asyncio
async def test_startup_check_root_principal_cannot_be_simulated(
    client: SqsSnsClient, stubs: Stubs
) -> None:
    stubs.add("sts", "get_caller_identity", {**IDENTITY, "Arn": "arn:aws:iam::123456789012:root"})
    report = await client.verify_credentials()
    assert report.ok and any("root" in w for w in report.warnings)


@pytest.mark.asyncio
async def test_startup_check_fails_without_valid_credentials(
    client: SqsSnsClient, stubs: Stubs
) -> None:
    stubs.error("sts", "get_caller_identity", "InvalidClientTokenId", status=403)
    report = await client.verify_credentials()
    assert not report.ok and "unauthorized" in report.reasons[0]
