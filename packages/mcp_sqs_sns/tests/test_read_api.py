"""T-060: the six tools' behaviour (FR-010/AC-001, FR-010/AC-002, FR-015/AC-001)."""

from __future__ import annotations

import pytest
from mcp_common.errors import ErrorCode, ToolError
from mcp_sqs_sns.read_api import SqsSnsReadApi
from sqs_helpers import (
    ACCOUNT,
    DLQ,
    DLQ_ARN,
    DLQ_URL,
    QUEUE,
    QUEUE_ARN,
    QUEUE_URL,
    REGION,
    SUB_ARN,
    TOPIC_ARN,
    Stubs,
    queue_attributes,
    queue_url,
    subscription,
    topic_arn,
    topic_attributes,
)


def _cursor_of(outcome) -> str | None:
    return outcome.result.meta.next_cursor


# -- sqs_list_queues -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_010_AC_001_list_queues_returns_name_url_arn_and_citation(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add(
        "sqs", "list_queues", {"QueueUrls": [QUEUE_URL, DLQ_URL]},
        {"QueueNamePrefix": "payment", "MaxResults": 100},
    )  # fmt: skip
    result = (await read_api.list_queues(name_prefix="payment")).result
    assert result.status.value == "ok" and result.meta.source.value == "sqs"
    assert [i["queue_name"] for i in result.items] == [QUEUE, DLQ]
    assert result.items[0]["arn"] == QUEUE_ARN and result.items[0]["queue_url"] == QUEUE_URL
    assert result.citations[0].locator == {"queue_arn": QUEUE_ARN}
    assert result.citations[0].uri is None
    assert [i["citation_ref"] for i in result.items] == [0, 1]


@pytest.mark.asyncio
async def test_FR_010_AC_002_list_queues_without_match_is_empty(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sqs", "list_queues", {})
    outcome = await read_api.list_queues(name_prefix="nope")
    assert outcome.result.status.value == "empty" and outcome.result.items == []


@pytest.mark.asyncio
async def test_list_queues_cursor_resumes_a_page_without_skipping_or_repeating(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    urls = [queue_url(f"q{i}") for i in range(5)]
    stubs.add("sqs", "list_queues", {"QueueUrls": urls, "NextToken": "T2"})
    first = await read_api.list_queues(limit=2)
    assert [i["queue_name"] for i in first.result.items] == ["q0", "q1"]
    assert first.result.meta.has_more and _cursor_of(first)
    stubs.add("sqs", "list_queues", {"QueueUrls": urls, "NextToken": "T2"}, {"MaxResults": 100})
    second = await read_api.list_queues(limit=2, cursor=_cursor_of(first))
    assert [i["queue_name"] for i in second.result.items] == ["q2", "q3"]
    # third call: the rest of page 1 (q4), then page 2 (q5) fills the limit exactly
    stubs.add("sqs", "list_queues", {"QueueUrls": urls, "NextToken": "T2"}, {"MaxResults": 100})
    stubs.add(
        "sqs", "list_queues", {"QueueUrls": [queue_url("q5")]},
        {"MaxResults": 100, "NextToken": "T2"},
    )  # fmt: skip
    third = await read_api.list_queues(limit=2, cursor=_cursor_of(second))
    assert [i["queue_name"] for i in third.result.items] == ["q4", "q5"]
    assert third.result.meta.has_more is False
    stubs.assert_all_consumed()


@pytest.mark.asyncio
async def test_list_queues_rejects_bad_limits_prefix_and_cursor(read_api: SqsSnsReadApi) -> None:
    for kwargs, field in (
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"name_prefix": "x" * 81}, "name_prefix"),
        ({"cursor": "not-a-cursor!!"}, "cursor"),
    ):
        with pytest.raises(ToolError) as exc:
            await read_api.list_queues(**kwargs)
        assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == field


@pytest.mark.asyncio
async def test_a_cursor_with_a_non_integer_skip_is_invalid(read_api: SqsSnsReadApi) -> None:
    from mcp_common.tooling import encode_cursor

    with pytest.raises(ToolError) as exc:
        await read_api.list_queues(cursor=encode_cursor({"t": "x", "s": "many"}))
    assert exc.value.details["field"] == "cursor"


# -- sqs_get_queue_attributes --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_010_AC_001_queue_attributes_include_arn_and_approximate_count(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sqs", "get_queue_url", {"QueueUrl": QUEUE_URL}, {"QueueName": QUEUE})
    stubs.add(
        "sqs", "get_queue_attributes", queue_attributes(),
        {"QueueUrl": QUEUE_URL, "AttributeNames": ["All"]},
    )  # fmt: skip
    outcome = await read_api.get_queue_attributes(queue_name=QUEUE)
    result = outcome.result
    item = result.items[0]
    assert result.status.value == "ok"
    assert item["arn"] == QUEUE_ARN and item["approximate_number_of_messages"] == 1842
    assert item["approximate_number_of_messages_not_visible"] == 12
    assert item["visibility_timeout_s"] == 30 and item["message_retention_period_s"] == 345600
    assert item["maximum_message_size_bytes"] == 262144 and item["fifo_queue"] is False
    assert item["redrive_policy"] == {"deadLetterTargetArn": DLQ_ARN, "maxReceiveCount": 5}
    assert item["created_at"] == "2025-04-02T01:00:00+00:00"
    assert item["tags"] is None
    assert (
        "1842" in result.citations[0].label and ACCOUNT in result.citations[0].locator["queue_arn"]
    )
    assert any("xấp xỉ" in w for w in result.meta.warnings)
    assert result.meta.query_echo == {"queue_name": QUEUE, "include_tags": False}


@pytest.mark.asyncio
async def test_queue_attributes_with_tags_and_fifo(read_api: SqsSnsReadApi, stubs: Stubs) -> None:
    fifo_url = queue_url("orders.fifo")
    stubs.add("sqs", "get_queue_attributes", queue_attributes(FifoQueue="true"))
    stubs.add("sqs", "list_queue_tags", {"Tags": {"team": "payment", "owner": "a"}})
    outcome = await read_api.get_queue_attributes(queue_url=fifo_url, include_tags=True)
    item = outcome.result.items[0]
    assert item["fifo_queue"] is True and item["tags"] == {"team": "payment", "owner": "a"}
    assert item["queue_name"] == "orders.fifo"
    assert outcome.result.meta.query_echo["queue_url"] == fifo_url


@pytest.mark.asyncio
async def test_queue_url_wins_over_queue_name_when_both_are_given(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add(
        "sqs", "get_queue_attributes", queue_attributes(),
        {"QueueUrl": DLQ_URL, "AttributeNames": ["All"]},
    )  # fmt: skip
    outcome = await read_api.get_queue_attributes(queue_name=QUEUE, queue_url=DLQ_URL)
    assert outcome.result.items[0]["queue_name"] == DLQ
    stubs.assert_all_consumed()  # no GetQueueUrl was issued


@pytest.mark.asyncio
async def test_tags_are_redacted_like_any_other_free_text(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sqs", "get_queue_attributes", queue_attributes())
    stubs.add(
        "sqs", "list_queue_tags",
        {"Tags": {"deploy-token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}},
    )  # fmt: skip
    outcome = await read_api.get_queue_attributes(queue_url=QUEUE_URL, include_tags=True)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in str(outcome.result.items)
    assert outcome.result.meta.redactions >= 1


@pytest.mark.parametrize("code", ["AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist"])
@pytest.mark.asyncio
async def test_FR_010_AC_002_unknown_queue_name_is_not_found(
    read_api: SqsSnsReadApi, stubs: Stubs, code: str
) -> None:
    stubs.error("sqs", "get_queue_url", code, "The specified queue does not exist.")
    outcome = await read_api.get_queue_attributes(queue_name="nope")
    assert outcome.result.status.value == "not_found" and outcome.identifier == "Queue 'nope'"


@pytest.mark.asyncio
async def test_FR_010_AC_002_unknown_queue_url_is_not_found(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.error("sqs", "get_queue_attributes", "QueueDoesNotExist")
    outcome = await read_api.get_queue_attributes(queue_url=queue_url("gone"))
    assert outcome.result.status.value == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({}, "queue_name"),
        ({"queue_name": "bad name!"}, "queue_name"),
        ({"queue_url": "ftp://x/y/z"}, "queue_url"),
        ({"queue_url": "https://sqs.example.test/onlyonesegment"}, "queue_url"),
    ],
)
@pytest.mark.asyncio
async def test_queue_attribute_inputs_are_validated(
    read_api: SqsSnsReadApi, kwargs: dict, field: str
) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_queue_attributes(**kwargs)
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == field


@pytest.mark.asyncio
async def test_other_aws_errors_propagate(read_api: SqsSnsReadApi, stubs: Stubs) -> None:
    stubs.error("sqs", "get_queue_url", "AccessDenied", status=403)
    with pytest.raises(ToolError) as exc:
        await read_api.get_queue_attributes(queue_name=QUEUE)
    assert exc.value.code == ErrorCode.FORBIDDEN


# -- sqs_list_dead_letter_source_queues ----------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_sources_are_listed_with_citations(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sqs", "get_queue_url", {"QueueUrl": DLQ_URL})
    stubs.add(
        "sqs", "list_dead_letter_source_queues", {"queueUrls": [QUEUE_URL]},
        {"QueueUrl": DLQ_URL, "MaxResults": 100},
    )  # fmt: skip
    result = (await read_api.list_dead_letter_source_queues(queue_name=DLQ)).result
    assert result.status.value == "ok" and result.items[0]["queue_name"] == QUEUE
    assert result.citations[0].locator == {"queue_arn": QUEUE_ARN}


@pytest.mark.asyncio
async def test_dead_letter_sources_empty_when_nothing_points_at_the_queue(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sqs", "list_dead_letter_source_queues", {"queueUrls": []})
    outcome = await read_api.list_dead_letter_source_queues(queue_url=DLQ_URL)
    assert outcome.result.status.value == "empty"
    assert DLQ in (outcome.query_description or "")


@pytest.mark.asyncio
async def test_dead_letter_sources_unknown_queue_is_not_found(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.error("sqs", "get_queue_url", "AWS.SimpleQueueService.NonExistentQueue")
    assert (
        await read_api.list_dead_letter_source_queues(queue_name="nope")
    ).result.status.value == "not_found"
    stubs.error("sqs", "list_dead_letter_source_queues", "QueueDoesNotExist")
    assert (
        await read_api.list_dead_letter_source_queues(queue_url=queue_url("gone"))
    ).result.status.value == "not_found"


@pytest.mark.asyncio
async def test_dead_letter_sources_validate_input(read_api: SqsSnsReadApi) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.list_dead_letter_source_queues()
    assert exc.value.details["field"] == "queue_name"
    with pytest.raises(ToolError) as limit:
        await read_api.list_dead_letter_source_queues(queue_name=DLQ, limit=500)
    assert limit.value.details["field"] == "limit"


# -- sns_list_topics -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_010_AC_001_list_topics_filters_by_name_prefix_in_the_arn(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add(
        "sns", "list_topics",
        {"Topics": [{"TopicArn": topic_arn("payment-events")}, {"TopicArn": topic_arn("orders")},
                    {"TopicArn": topic_arn("payment-refunds")}]},
    )  # fmt: skip
    result = (await read_api.list_topics(name_prefix="payment")).result
    assert [i["name"] for i in result.items] == ["payment-events", "payment-refunds"]
    assert result.items[0]["topic_arn"] == TOPIC_ARN
    assert result.meta.source.value == "sns"
    assert result.citations[0].locator == {"topic_arn": TOPIC_ARN}


@pytest.mark.asyncio
async def test_list_topics_follows_pages_until_the_limit_is_filled(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sns", "list_topics", {"Topics": [{"TopicArn": topic_arn("a1")}], "NextToken": "P2"})
    stubs.add("sns", "list_topics", {"Topics": [{"TopicArn": topic_arn("payment-x")}],
                                     "NextToken": "P3"}, {"NextToken": "P2"})  # fmt: skip
    stubs.add("sns", "list_topics", {"Topics": [{"TopicArn": topic_arn("payment-y")}]},
              {"NextToken": "P3"})  # fmt: skip
    result = (await read_api.list_topics(name_prefix="payment", limit=5)).result
    assert [i["name"] for i in result.items] == ["payment-x", "payment-y"]
    assert result.meta.has_more is False
    stubs.assert_all_consumed()


@pytest.mark.asyncio
async def test_list_topics_resumes_inside_a_page(read_api: SqsSnsReadApi, stubs: Stubs) -> None:
    topics = [{"TopicArn": topic_arn(f"t{i}")} for i in range(3)]
    stubs.add("sns", "list_topics", {"Topics": topics})
    first = await read_api.list_topics(limit=2)
    assert [i["name"] for i in first.result.items] == ["t0", "t1"]
    stubs.add("sns", "list_topics", {"Topics": topics})
    second = await read_api.list_topics(limit=2, cursor=_cursor_of(first))
    assert [i["name"] for i in second.result.items] == ["t2"]
    assert second.result.meta.has_more is False


@pytest.mark.asyncio
async def test_list_topics_stops_at_the_page_cap_and_hands_back_a_cursor(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    for _ in range(20):  # 20 pages with no match for the prefix
        stubs.add("sns", "list_topics", {"Topics": [{"TopicArn": topic_arn("other")}],
                                         "NextToken": "more"})  # fmt: skip
    outcome = await read_api.list_topics(name_prefix="payment", limit=5)
    assert outcome.result.status.value == "empty"  # nothing found *so far*...
    assert outcome.result.meta.has_more and _cursor_of(outcome)  # ...but the scan can continue


@pytest.mark.asyncio
async def test_list_topics_empty_and_validation(read_api: SqsSnsReadApi, stubs: Stubs) -> None:
    stubs.add("sns", "list_topics", {"Topics": []})
    assert (await read_api.list_topics()).result.status.value == "empty"
    with pytest.raises(ToolError) as exc:
        await read_api.list_topics(name_prefix="x" * 257)
    assert exc.value.details["field"] == "name_prefix"
    with pytest.raises(ToolError) as limit:
        await read_api.list_topics(limit=0)
    assert limit.value.source == "sns"


# -- sns_get_topic_attributes --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_010_AC_001_topic_attributes_cite_the_arn(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sns", "get_topic_attributes", topic_attributes(), {"TopicArn": TOPIC_ARN})
    result = (await read_api.get_topic_attributes(topic_arn=TOPIC_ARN)).result
    item = result.items[0]
    assert item["topic_arn"] == TOPIC_ARN and item["name"] == "payment-events"
    assert item["display_name"] == "Payment Events"
    assert (item["subscriptions_confirmed"], item["subscriptions_pending"]) == (3, 0)
    assert item["subscriptions_deleted"] == 1 and item["fifo_topic"] is False
    assert item["effective_delivery_policy"]["http"]["defaultHealthyRetryPolicy"] == {
        "numRetries": 3
    }
    assert result.citations[0].locator == {"topic_arn": TOPIC_ARN}
    assert "3 subscriptions" in result.citations[0].label


@pytest.mark.asyncio
async def test_FR_010_AC_002_unknown_topic_is_not_found(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.error("sns", "get_topic_attributes", "NotFound", "Topic does not exist", status=404)
    outcome = await read_api.get_topic_attributes(topic_arn=TOPIC_ARN)
    assert outcome.result.status.value == "not_found" and TOPIC_ARN in (outcome.identifier or "")


@pytest.mark.asyncio
async def test_topic_attribute_errors_propagate_and_the_arn_is_validated(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.error("sns", "get_topic_attributes", "AuthorizationError", status=403)
    with pytest.raises(ToolError) as exc:
        await read_api.get_topic_attributes(topic_arn=TOPIC_ARN)
    assert exc.value.code == ErrorCode.FORBIDDEN
    for bad in ("payment-events", "arn:aws:sqs:r:1:q", "arn:aws:sns:" + "x" * 600):
        with pytest.raises(ToolError) as invalid:
            await read_api.get_topic_attributes(topic_arn=bad)
        assert invalid.value.details["field"] == "topic_arn"


# -- sns_list_subscriptions_by_topic -------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriptions_show_the_fan_out_targets(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add(
        "sns", "list_subscriptions_by_topic", {"Subscriptions": [subscription()]},
        {"TopicArn": TOPIC_ARN},
    )  # fmt: skip
    result = (await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN)).result
    item = result.items[0]
    assert item["subscription_arn"] == SUB_ARN and item["protocol"] == "sqs"
    assert item["endpoint"] == QUEUE_ARN and item["owner"] == ACCOUNT
    assert result.citations[0].locator == {"subscription_arn": SUB_ARN}
    assert f"→ sqs {QUEUE}" in result.citations[0].label
    assert result.meta.warnings == []


@pytest.mark.asyncio
async def test_subscription_endpoints_are_masked_for_pii_and_credentials(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    subs = [
        subscription("email", "alice@example.com", f"{TOPIC_ARN}:aaa"),
        subscription("sms", "+84901234567", f"{TOPIC_ARN}:bbb"),
        subscription(
            "https", "https://user:pw@hooks.example.com/x?token=SECRET123", f"{TOPIC_ARN}:ccc"
        ),
        subscription("lambda", f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:f", f"{TOPIC_ARN}:ddd"),
    ]
    stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": subs})
    result = (await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN)).result
    endpoints = [i["endpoint"] for i in result.items]
    assert endpoints[0] == "a***@example.com"
    assert "84901234567" not in endpoints[1] and endpoints[1].startswith("+84")
    assert endpoints[2] == "https://hooks.example.com/x"
    assert endpoints[3].endswith("function:f")
    blob = str(result.model_dump())
    assert "alice@example.com" not in blob and "SECRET123" not in blob and "pw@" not in blob
    assert any("che bớt" in w for w in result.meta.warnings)


@pytest.mark.asyncio
async def test_pending_subscription_is_cited_by_topic_and_endpoint(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    pending = subscription("sqs", QUEUE_ARN, "PendingConfirmation")
    stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": [pending]})
    result = (await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN)).result
    assert result.items[0]["subscription_arn"] == "PendingConfirmation"
    assert result.citations[0].locator == {"topic_arn": TOPIC_ARN, "endpoint": QUEUE_ARN}


@pytest.mark.asyncio
async def test_subscriptions_empty_not_found_and_validation(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": []})
    assert (
        await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN)
    ).result.status.value == "empty"
    stubs.error("sns", "list_subscriptions_by_topic", "NotFound", status=404)
    assert (
        await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN)
    ).result.status.value == "not_found"
    with pytest.raises(ToolError):
        await read_api.list_subscriptions_by_topic(topic_arn="nope")
    with pytest.raises(ToolError):
        await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN, limit=101)


@pytest.mark.asyncio
async def test_subscriptions_cursor_pages_through_a_long_topic(
    read_api: SqsSnsReadApi, stubs: Stubs
) -> None:
    subs = [subscription(arn=f"{TOPIC_ARN}:{i:04d}") for i in range(3)]
    stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": subs})
    first = await read_api.list_subscriptions_by_topic(topic_arn=TOPIC_ARN, limit=2)
    assert first.result.meta.has_more
    stubs.add("sns", "list_subscriptions_by_topic", {"Subscriptions": subs})
    second = await read_api.list_subscriptions_by_topic(
        topic_arn=TOPIC_ARN, limit=2, cursor=_cursor_of(first)
    )
    assert [i["subscription_arn"] for i in second.result.items] == [f"{TOPIC_ARN}:0002"]
