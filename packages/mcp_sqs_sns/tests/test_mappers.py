"""T-060: pure mappers (queue ARN derivation, endpoint masking, tolerant attribute parsing)."""

from __future__ import annotations

import pytest

from mcp_sqs_sns import mappers


@pytest.mark.parametrize(
    ("url", "arn"),
    [
        (
            "https://sqs.ap-southeast-1.amazonaws.com/123456789012/q",
            "arn:aws:sqs:ap-southeast-1:123456789012:q",
        ),
        (
            "https://sqs.cn-north-1.amazonaws.com.cn/123456789012/q",
            "arn:aws-cn:sqs:cn-north-1:123456789012:q",
        ),
        ("http://localhost:4566/000000000000/q", "arn:aws:sqs:eu-west-1:000000000000:q"),
        ("https://queue.example/only-one-segment", None),
    ],
)
def test_queue_arn_from_url(url: str, arn: str | None) -> None:
    assert mappers.queue_arn_from_url(url, "eu-west-1") == arn


def test_queue_without_a_derivable_arn_is_cited_by_url() -> None:
    item, citation = mappers.map_queue(
        "https://queue.example/solo", default_region="r", citation_ref=0
    )
    assert item["arn"] is None and citation.locator == {"queue_url": "https://queue.example/solo"}


@pytest.mark.parametrize("value", [None, "", "not-a-number", "9" * 40])
def test_epoch_to_iso_tolerates_bad_values(value) -> None:
    assert mappers.epoch_to_iso(value) is None


def test_attributes_with_missing_or_garbled_values_do_not_crash() -> None:
    item, _ = mappers.map_queue_attributes(
        "https://sqs.r.amazonaws.com/1/q",
        {
            "ApproximateNumberOfMessages": "oops",
            "RedrivePolicy": "{not json",
            "VisibilityTimeout": "-5",
        },
        default_region="r", tags=None, citation_ref=0,
    )  # fmt: skip
    assert item["approximate_number_of_messages"] == 0
    assert item["redrive_policy"] is None and item["visibility_timeout_s"] == 0
    assert item["arn"] == "arn:aws:sqs:r:1:q"
    list_policy, _ = mappers.map_queue_attributes(
        "https://sqs.r.amazonaws.com/1/q", {"RedrivePolicy": "[1]"},
        default_region="r", tags=None, citation_ref=0,
    )  # fmt: skip
    assert list_policy["redrive_policy"] is None


def test_topic_attributes_tolerate_missing_counters_and_bad_policy() -> None:
    item, citation = mappers.map_topic_attributes(
        "arn:aws:sns:r:1:t",
        {"EffectiveDeliveryPolicy": "{bad", "FifoTopic": "true"},
        citation_ref=0,
    )
    assert item["subscriptions_confirmed"] is None and item["effective_delivery_policy"] is None
    assert item["fifo_topic"] is True and citation.label == "topic t"
    ok, _ = mappers.map_topic_attributes(
        "arn:aws:sns:r:1:t", {"EffectiveDeliveryPolicy": "[1]"}, citation_ref=0
    )
    assert ok["effective_delivery_policy"] is None


@pytest.mark.parametrize(
    ("protocol", "endpoint", "expected", "masked"),
    [
        ("email", "a@b.co", "a***@b.co", True),
        ("email-json", "alice@b.co", "a***@b.co", True),
        ("sms", "+84901234567", "+84***67", True),
        ("sms", "1234", "***", True),
        ("https", "https://h.example:8443/p?q=1", "https://h.example:8443/p", True),
        ("https", "https://h.example/p", "https://h.example/p", False),
        ("sqs", "arn:aws:sqs:r:1:q", "arn:aws:sqs:r:1:q", False),
        ("sqs", None, None, False),
    ],
)
def test_mask_endpoint(protocol: str, endpoint, expected, masked: bool) -> None:
    assert mappers.mask_endpoint(protocol, endpoint) == (expected, masked)
