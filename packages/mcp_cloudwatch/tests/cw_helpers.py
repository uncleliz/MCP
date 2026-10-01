"""botocore Stubber plumbing + payload builders for the mcp-cloudwatch tests.

Payloads are hand-written to the shape of the AWS API reference (no AWS account is reachable
from this container); they have NOT been captured from real AWS. `test_integration.py`
(marker `live`) covers the real thing. Stubbed clients are *real* boto3 clients, so parameter
validation (names, types, required fields) is checked by botocore itself.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.stub import ANY, Stubber

REGION = "ap-southeast-1"
GROUP = "/aws/lambda/payment-worker"
T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)
T1_MS = int(T1.timestamp() * 1000)
WINDOW = {"time_from": T0, "time_to": T1}
WINDOW_ARGS = {"time_from": "2026-09-30T10:00:00Z", "time_to": "2026-09-30T12:00:00Z"}
ANY_PARAM = ANY


class Stubs:
    """Real boto3 clients (dummy credentials) each wrapped in an activated Stubber."""

    def __init__(self) -> None:
        self.clients: dict[str, Any] = {}
        self.stubbers: dict[str, Stubber] = {}
        for service in ("logs", "cloudwatch", "sts", "iam"):
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


def log_event(
    message: str = "ERROR payment gateway timeout after 3 retries",
    ts: datetime = datetime(2026, 9, 30, 10, 41, 12, tzinfo=UTC),
    stream: str = "2026/09/30/[$LATEST]abc123",
    event_id: str = "38512",
) -> dict[str, Any]:
    return {
        "logStreamName": stream,
        "timestamp": int(ts.timestamp() * 1000),
        "message": message,
        "ingestionTime": int(ts.timestamp() * 1000) + 2000,
        "eventId": event_id,
    }


def alarm(name: str = "payment-worker-error-rate", state: str = "ALARM") -> dict[str, Any]:
    return {
        "AlarmName": name,
        "AlarmArn": f"arn:aws:cloudwatch:{REGION}:123456789012:alarm:{name}",
        "StateValue": state,
        "StateReason": "Threshold Crossed: 3 datapoints were greater than the threshold (5.0).",
        "StateUpdatedTimestamp": datetime(2026, 9, 30, 10, 40, tzinfo=UTC),
        "Namespace": "PaymentService",
        "MetricName": "ErrorRate",
        "Dimensions": [{"Name": "Service", "Value": "payment"}],
        "ComparisonOperator": "GreaterThanThreshold",
        "Threshold": 5.0,
        "Period": 300,
        "EvaluationPeriods": 3,
        "ActionsEnabled": True,
    }


SQS_METRIC = {
    "Namespace": "AWS/SQS",
    "MetricName": "ApproximateAgeOfOldestMessage",
    "Dimensions": [{"Name": "QueueName", "Value": "payment-events"}],
}
DIMS = [{"name": "QueueName", "value": "payment-events"}]


def _insights_args() -> dict[str, Any]:
    return {
        "log_group_names": [GROUP],
        "query": "fields @timestamp, @message | filter @message like /Timeout/ | limit 20",
        **WINDOW_ARGS,
    }


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("cloudwatch_list_log_groups", {"name_prefix": "/aws/lambda"}),
    ("cloudwatch_filter_log_events", {"log_group_name": GROUP, **WINDOW_ARGS}),
    ("cloudwatch_run_logs_insights", _insights_args()),
    ("cloudwatch_list_metrics", {"namespace": "AWS/SQS", "dimensions": DIMS}),
    (
        "cloudwatch_get_metric_data",
        {"namespace": "AWS/SQS", "metric_name": "ApproximateAgeOfOldestMessage",
         "dimensions": DIMS, "stat": "Maximum", **WINDOW_ARGS},
    ),
    ("cloudwatch_describe_alarms", {"state_value": "ALARM"}),
    (
        "cloudwatch_describe_alarm_history",
        {"alarm_name": "payment-worker-error-rate", "time_from": "2026-09-30T09:00:00Z",
         "time_to": "2026-09-30T13:00:00Z"},
    ),
]  # fmt: skip


def stub_ok(stubs: Stubs, tool: str) -> None:
    """Queue the AWS responses a happy-path call of `tool` needs."""
    if tool == "cloudwatch_list_log_groups":
        stubs.add("logs", "describe_log_groups", {"logGroups": [{"logGroupName": GROUP}]})
    elif tool == "cloudwatch_filter_log_events":
        stubs.add("logs", "filter_log_events", {"events": [log_event()]})
    elif tool == "cloudwatch_run_logs_insights":
        stubs.add("logs", "start_query", {"queryId": "q1"})
        row = [{"field": "@timestamp", "value": "2026-09-30 10:41:12.000"},
               {"field": "@message", "value": "ERROR payment gateway timeout"}]  # fmt: skip
        stubs.add("logs", "get_query_results", {"status": "Complete", "results": [row]})
    elif tool == "cloudwatch_list_metrics":
        stubs.add("cloudwatch", "list_metrics", {"Metrics": [SQS_METRIC]})
    elif tool == "cloudwatch_get_metric_data":
        stubs.add("cloudwatch", "list_metrics", {"Metrics": [SQS_METRIC]})
        stubs.add(
            "cloudwatch", "get_metric_data",
            {"MetricDataResults": [{"Id": "m1", "Timestamps": [T0], "Values": [431.0]}]},
        )  # fmt: skip
    elif tool == "cloudwatch_describe_alarms":
        stubs.add("cloudwatch", "describe_alarms", {"MetricAlarms": [alarm()]})
    elif tool == "cloudwatch_describe_alarm_history":
        stubs.add("cloudwatch", "describe_alarms", {"MetricAlarms": [alarm()]})
        stubs.add(
            "cloudwatch", "describe_alarm_history",
            {"AlarmHistoryItems": [{
                "AlarmName": "payment-worker-error-rate", "Timestamp": T0,
                "HistoryItemType": "StateUpdate", "HistorySummary": "OK -> ALARM",
                "HistoryData": '{"oldState":{"stateValue":"OK"},"newState":{"stateValue":"ALARM"}}',
            }]},
        )  # fmt: skip
    else:  # pragma: no cover - test authoring error
        raise AssertionError(tool)
