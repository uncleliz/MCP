"""SQS/SNS payloads -> contract item schemas + `Citation` (FR-010/AC-001, FR-015).

Pure functions, no I/O. Citations carry the queue/topic/subscription ARN in `locator` (there is
no web URL, so `uri` is `None`). Nothing here ever touches a message body.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp_common.envelope import Citation, SourceType

__all__ = [
    "arn_name",
    "epoch_to_iso",
    "mask_endpoint",
    "map_queue",
    "map_queue_attributes",
    "map_subscription",
    "map_topic",
    "map_topic_attributes",
    "queue_arn_from_url",
    "queue_name_from_url",
]

_REGION_IN_HOST = re.compile(r"(?:^|\.)sqs\.([a-z0-9-]+)\.amazonaws\.com")


def _cite(source: SourceType, label: str, locator: dict[str, Any]) -> Citation:
    return Citation(
        source_type=source,
        label=label[:512],
        uri=None,
        locator=locator,
        retrieved_at=datetime.now(UTC),
    )


def epoch_to_iso(value: str | int | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(str(value)), UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _int(value: Any) -> int | None:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def queue_name_from_url(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def queue_arn_from_url(url: str, default_region: str) -> str | None:
    """`https://sqs.<region>.amazonaws.com/<account>/<name>` -> the queue ARN (None if the URL
    does not have the `/<account>/<name>` shape). LocalStack/moto URLs use the default region."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:  # noqa: PLR2004
        return None
    account, name = segments[-2], segments[-1]
    match = _REGION_IN_HOST.search(parts.hostname or "")
    region = match.group(1) if match else default_region
    partition = "aws-cn" if (parts.hostname or "").endswith(".amazonaws.com.cn") else "aws"
    return f"arn:{partition}:sqs:{region}:{account}:{name}"


def arn_name(arn: str) -> str:
    """The resource name of an ARN (last `:` segment): topic name, queue name."""
    return arn.rsplit(":", 1)[-1]


def map_queue(
    url: str, *, default_region: str, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    name = queue_name_from_url(url)
    arn = queue_arn_from_url(url, default_region)
    item = {"queue_name": name, "queue_url": url, "arn": arn, "citation_ref": citation_ref}
    locator = {"queue_arn": arn} if arn else {"queue_url": url}
    return item, _cite(SourceType.SQS, f"queue {name}", locator)


def _redrive(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    # `maxReceiveCount` arrives as a string on some endpoints; the contract example uses an int.
    count = parsed.get("maxReceiveCount")
    if isinstance(count, str) and count.isdigit():
        parsed["maxReceiveCount"] = int(count)
    return parsed


def map_queue_attributes(
    url: str,
    attributes: dict[str, str],
    *,
    default_region: str,
    tags: dict[str, str] | None,
    citation_ref: int,
) -> tuple[dict[str, Any], Citation]:
    name = queue_name_from_url(url)
    arn = attributes.get("QueueArn") or queue_arn_from_url(url, default_region) or ""
    messages = _int(attributes.get("ApproximateNumberOfMessages")) or 0
    item = {
        "queue_name": name,
        "queue_url": url,
        "arn": arn,
        "approximate_number_of_messages": messages,
        "approximate_number_of_messages_not_visible": _int(
            attributes.get("ApproximateNumberOfMessagesNotVisible")
        ),
        "approximate_number_of_messages_delayed": _int(
            attributes.get("ApproximateNumberOfMessagesDelayed")
        ),
        "visibility_timeout_s": _int(attributes.get("VisibilityTimeout")),
        "message_retention_period_s": _int(attributes.get("MessageRetentionPeriod")),
        "maximum_message_size_bytes": _int(attributes.get("MaximumMessageSize")),
        "fifo_queue": (attributes.get("FifoQueue") == "true")
        if "FifoQueue" in attributes
        else False,
        "redrive_policy": _redrive(attributes.get("RedrivePolicy")),
        "created_at": epoch_to_iso(attributes.get("CreatedTimestamp")),
        "last_modified_at": epoch_to_iso(attributes.get("LastModifiedTimestamp")),
        "tags": tags,
        "citation_ref": citation_ref,
    }
    return item, _cite(
        SourceType.SQS, f"queue {name} (≈{messages} messages)", {"queue_arn": arn or url}
    )


def map_topic(arn: str, *, citation_ref: int) -> tuple[dict[str, Any], Citation]:
    name = arn_name(arn)
    item = {"topic_arn": arn, "name": name, "citation_ref": citation_ref}
    return item, _cite(SourceType.SNS, f"topic {name}", {"topic_arn": arn})


def _json_or_none(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def map_topic_attributes(
    arn: str, attributes: dict[str, str], *, citation_ref: int
) -> tuple[dict[str, Any], Citation]:
    name = arn_name(arn)
    confirmed = _int(attributes.get("SubscriptionsConfirmed"))
    item = {
        "topic_arn": attributes.get("TopicArn") or arn,
        "name": name,
        "display_name": attributes.get("DisplayName") or None,
        "subscriptions_confirmed": confirmed,
        "subscriptions_pending": _int(attributes.get("SubscriptionsPending")),
        "subscriptions_deleted": _int(attributes.get("SubscriptionsDeleted")),
        "fifo_topic": (attributes.get("FifoTopic") == "true")
        if "FifoTopic" in attributes
        else False,
        "effective_delivery_policy": _json_or_none(attributes.get("EffectiveDeliveryPolicy")),
        "citation_ref": citation_ref,
    }
    label = f"topic {name}" + (f" ({confirmed} subscriptions)" if confirmed is not None else "")
    return item, _cite(SourceType.SNS, label, {"topic_arn": arn})


def mask_endpoint(protocol: str, endpoint: str | None) -> tuple[str | None, bool]:
    """Minimise PII/secrets in a subscription endpoint. Returns `(value, was_masked)`.

    email/sms endpoints are addresses/phone numbers; http(s) endpoints may embed credentials
    in the userinfo or a token in the query string. Queue/Lambda/topic ARNs pass through.
    """
    if not endpoint:
        return endpoint, False
    lowered = protocol.lower()
    if lowered in {"email", "email-json"} and "@" in endpoint:
        local, _, domain = endpoint.partition("@")
        return f"{local[:1]}***@{domain}", True
    if lowered == "sms":
        return f"{endpoint[:3]}***{endpoint[-2:]}" if len(endpoint) > 5 else "***", True  # noqa: PLR2004
    if lowered in {"http", "https"}:
        parts = urlsplit(endpoint)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        cleaned = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
        return cleaned, cleaned != endpoint
    return endpoint, False


def map_subscription(
    raw: dict[str, Any], *, topic_arn: str, citation_ref: int
) -> tuple[dict[str, Any], Citation, bool]:
    protocol = str(raw.get("Protocol") or "unknown")
    endpoint, masked = mask_endpoint(protocol, raw.get("Endpoint"))
    subscription_arn = str(raw.get("SubscriptionArn") or "")
    item = {
        "subscription_arn": subscription_arn,
        "topic_arn": str(raw.get("TopicArn") or topic_arn),
        "protocol": protocol,
        "endpoint": endpoint,
        "owner": raw.get("Owner") or None,
        "citation_ref": citation_ref,
    }
    target = arn_name(endpoint) if endpoint and endpoint.startswith("arn:") else (endpoint or "?")
    if subscription_arn.startswith("arn:"):
        locator: dict[str, Any] = {"subscription_arn": subscription_arn}
    else:  # "PendingConfirmation" / "Deleted": not an ARN, so identify it by topic + endpoint
        locator = {"topic_arn": topic_arn, "endpoint": endpoint}
    label = f"subscription {arn_name(topic_arn)} → {protocol} {target}"
    return item, _cite(SourceType.SNS, label, locator), masked
