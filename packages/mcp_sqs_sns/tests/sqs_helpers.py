"""botocore Stubber plumbing + payload builders for the mcp-sqs-sns tests.

Payloads are hand-written to the shape of the AWS API reference; `test_moto_integration.py`
(a real HTTP server speaking the AWS protocol) and `test_integration.py` (marker `live`,
LocalStack) cover the wire level. Stubbed clients are *real* boto3 clients, so parameter
validation (names, types, required fields) is checked by botocore itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config
from botocore.stub import Stubber

REGION = "ap-southeast-1"
ACCOUNT = "123456789012"
SQS_HOST = f"https://sqs.{REGION}.amazonaws.com"
QUEUE = "payment-events"
QUEUE_URL = f"{SQS_HOST}/{ACCOUNT}/{QUEUE}"
QUEUE_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{QUEUE}"
DLQ = "payment-events-dlq"
DLQ_URL = f"{SQS_HOST}/{ACCOUNT}/{DLQ}"
DLQ_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{DLQ}"
TOPIC_ARN = f"arn:aws:sns:{REGION}:{ACCOUNT}:payment-events"
SUB_ARN = f"{TOPIC_ARN}:1a2b3c4d-0000-0000-0000-000000000001"


class Stubs:
    """Real boto3 clients (dummy credentials) each wrapped in an activated Stubber."""

    def __init__(self) -> None:
        self.clients: dict[str, Any] = {}
        self.stubbers: dict[str, Stubber] = {}
        for service in ("sqs", "sns", "sts", "iam"):
            client = boto3.client(
                service,
                region_name=REGION,
                aws_access_key_id="AKIDEXAMPLEEXAMPLEXX",
                aws_secret_access_key="not-a-real-secret",  # noqa: S106
                config=Config(retries={"max_attempts": 1}),
            )
            self.clients[service] = client
            self.stubbers[service] = Stubber(client)
            self.stubbers[service].activate()

    def factory(self) -> Callable[[str], Any]:
        return lambda service: self.clients[service]

    def add(
        self, service: str, operation: str, response: dict[str, Any], expected: Any = None
    ) -> None:
        self.stubbers[service].add_response(operation, response, expected)

    def error(
        self, service: str, operation: str, code: str, message: str = "boom", status: int = 400
    ) -> None:
        self.stubbers[service].add_client_error(
            operation, service_error_code=code, service_message=message, http_status_code=status
        )

    def assert_all_consumed(self) -> None:
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()


def queue_attributes(**overrides: str) -> dict[str, Any]:
    attributes = {
        "QueueArn": QUEUE_ARN,
        "ApproximateNumberOfMessages": "1842",
        "ApproximateNumberOfMessagesNotVisible": "12",
        "ApproximateNumberOfMessagesDelayed": "0",
        "VisibilityTimeout": "30",
        "MessageRetentionPeriod": "345600",
        "MaximumMessageSize": "262144",
        "CreatedTimestamp": "1743555600",
        "LastModifiedTimestamp": "1756688400",
        "RedrivePolicy": f'{{"deadLetterTargetArn":"{DLQ_ARN}","maxReceiveCount":"5"}}',
    }
    attributes.update(overrides)
    return {"Attributes": attributes}


def topic_attributes(**overrides: str) -> dict[str, Any]:
    attributes = {
        "TopicArn": TOPIC_ARN,
        "DisplayName": "Payment Events",
        "SubscriptionsConfirmed": "3",
        "SubscriptionsPending": "0",
        "SubscriptionsDeleted": "1",
        "EffectiveDeliveryPolicy": '{"http":{"defaultHealthyRetryPolicy":{"numRetries":3}}}',
    }
    attributes.update(overrides)
    return {"Attributes": attributes}


def subscription(
    protocol: str = "sqs", endpoint: str = QUEUE_ARN, arn: str = SUB_ARN
) -> dict[str, str]:
    return {
        "SubscriptionArn": arn,
        "Owner": ACCOUNT,
        "Protocol": protocol,
        "Endpoint": endpoint,
        "TopicArn": TOPIC_ARN,
    }


def queue_url(name: str) -> str:
    return f"{SQS_HOST}/{ACCOUNT}/{name}"


def topic_arn(name: str) -> str:
    return f"arn:aws:sns:{REGION}:{ACCOUNT}:{name}"


# One happy-path call per tool, with the stub responses it consumes (used by contract tests).
OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("sqs_list_queues", {"name_prefix": "payment"}),
    ("sqs_get_queue_attributes", {"queue_name": QUEUE, "include_tags": True}),
    ("sqs_list_dead_letter_source_queues", {"queue_name": DLQ}),
    ("sns_list_topics", {"name_prefix": "payment"}),
    ("sns_get_topic_attributes", {"topic_arn": TOPIC_ARN}),
    ("sns_list_subscriptions_by_topic", {"topic_arn": TOPIC_ARN}),
]


def stub_ok(stubs: Stubs, tool: str) -> None:
    if tool == "sqs_list_queues":
        stubs.add("sqs", "list_queues", {"QueueUrls": [QUEUE_URL, DLQ_URL]})
    elif tool == "sqs_get_queue_attributes":
        stubs.add("sqs", "get_queue_url", {"QueueUrl": QUEUE_URL})
        stubs.add("sqs", "get_queue_attributes", queue_attributes())
        stubs.add("sqs", "list_queue_tags", {"Tags": {"team": "payment"}})
    elif tool == "sqs_list_dead_letter_source_queues":
        stubs.add("sqs", "get_queue_url", {"QueueUrl": DLQ_URL})
        stubs.add("sqs", "list_dead_letter_source_queues", {"queueUrls": [QUEUE_URL]})
    elif tool == "sns_list_topics":
        stubs.add("sns", "list_topics", {"Topics": [{"TopicArn": TOPIC_ARN}]})
    elif tool == "sns_get_topic_attributes":
        stubs.add("sns", "get_topic_attributes", topic_attributes())
    elif tool == "sns_list_subscriptions_by_topic":
        stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": [subscription()]})
    else:  # pragma: no cover
        raise AssertionError(tool)
