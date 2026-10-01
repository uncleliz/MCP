"""SQS/SNS tool layer: input validation, paging and shaping (no message-body access).

The six tools only ever read queue/topic **metadata**. Tool bounds (`limit <= 100`, name/ARN
formats) live here, the transport allowlist in `client.py`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from mcp_common.config import CommonSettings
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import (
    CallState,
    ToolOutcome,
    build_result,
    decode_cursor,
    encode_cursor,
    invalid_input,
    not_found_result,
)

from mcp_sqs_sns import mappers
from mcp_sqs_sns.client import SqsSnsClient
from mcp_sqs_sns.settings import Settings

__all__ = ["SqsSnsReadApi"]

_QUEUE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_TOPIC_ARN = re.compile(r"^arn:aws[a-zA-Z-]*:sns:")
_PAGE = 100  # fixed page size => a `{token, skip}` cursor stays valid when `limit` changes
_MAX_PAGES = 20
_APPROX_WARNING = "số message là xấp xỉ (ApproximateNumberOfMessages)"


def _is_not_found(exc: ToolError) -> bool:
    return exc.code == ErrorCode.UPSTREAM_ERROR and exc.details.get("upstream_status") == 404


class SqsSnsReadApi:
    def __init__(self, client: SqsSnsClient, common: CommonSettings, settings: Settings) -> None:
        self._client = client
        self._common = common
        self._settings = settings

    # -- validation helpers ------------------------------------------------------------

    @staticmethod
    def _limit(limit: int, source: str) -> None:
        if not 1 <= limit <= 100:  # noqa: PLR2004
            raise invalid_input("limit", "phải nằm trong khoảng 1..100", source)

    @staticmethod
    def _prefix(value: str | None, max_len: int, source: str) -> None:
        if value is not None and len(value) > max_len:
            raise invalid_input("name_prefix", f"tối đa {max_len} ký tự", source)

    def _state(self) -> CallState:
        return CallState(
            self._common.max_output_bytes, redact_disabled=self._common.redact_disabled
        )

    @staticmethod
    def _cursor_state(cursor: str | None, source: str) -> tuple[str | None, int]:
        state = decode_cursor(cursor, source=source)
        token, skip = state.get("t"), state.get("s", 0)
        if (
            (token is not None and not isinstance(token, str))
            or not isinstance(skip, int)
            or skip < 0
        ):
            raise invalid_input("cursor", "cursor không hợp lệ; dùng đúng meta.next_cursor", source)
        return token, skip

    async def _paged(
        self,
        fetch: Callable[[str | None], Awaitable[dict[str, Any]]],
        take: Callable[[dict[str, Any]], list[Any]],
        *,
        limit: int,
        cursor: str | None,
        source: str,
    ) -> tuple[list[Any], str | None]:
        """Collect up to `limit` matching entries across AWS pages.

        The cursor is `{t: token of the page being consumed, s: matches already returned from
        that page}`, so a page that holds more matches than `limit` is resumed exactly where it
        stopped instead of skipping or repeating entries.
        """
        token, skip = self._cursor_state(cursor, source)
        collected: list[Any] = []
        pages = 0
        while True:
            response = await fetch(token)
            matches = take(response)[skip:]
            room = limit - len(collected)
            if len(matches) > room:
                collected.extend(matches[:room])
                return collected, encode_cursor({"t": token, "s": skip + room})
            collected.extend(matches)
            next_token = response.get("NextToken")
            if not next_token:
                return collected, None
            token, skip, pages = next_token, 0, pages + 1
            if len(collected) >= limit or pages >= _MAX_PAGES:
                return collected, encode_cursor({"t": token, "s": 0})

    # -- queue resolution ----------------------------------------------------------------

    async def _resolve_queue(
        self, queue_name: str | None, queue_url: str | None
    ) -> tuple[str, str]:
        if queue_url:
            parts = urlsplit(queue_url)
            if (
                parts.scheme not in ("http", "https")
                or len([s for s in parts.path.split("/") if s]) < 2
            ):  # noqa: PLR2004
                raise invalid_input("queue_url", "không phải URL queue SQS hợp lệ", "sqs")
            return mappers.queue_name_from_url(queue_url), queue_url
        if not queue_name:
            raise invalid_input("queue_name", "phải có queue_name hoặc queue_url", "sqs")
        if not _QUEUE_NAME.match(queue_name):
            raise invalid_input("queue_name", "chỉ gồm chữ, số, '-', '_' và '.' (tối đa 80)", "sqs")
        response = await self._client.call("sqs", "GetQueueUrl", QueueName=queue_name)
        return queue_name, str(response["QueueUrl"])

    @staticmethod
    def _label(queue_name: str | None, queue_url: str | None) -> str:
        """Best name for messages before the queue is resolved (`queue_url` wins)."""
        if queue_url:
            return mappers.queue_name_from_url(queue_url) or queue_url
        return queue_name or ""

    @staticmethod
    def _not_found(
        source: SourceType, call: CallState, echo: dict[str, Any], identifier: str
    ) -> ToolOutcome:
        return ToolOutcome(
            not_found_result(source, started=call.started, query_echo=echo), identifier=identifier
        )

    # -- sqs_list_queues -----------------------------------------------------------------

    async def list_queues(
        self, *, name_prefix: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        self._limit(limit, "sqs")
        self._prefix(name_prefix, 80, "sqs")
        call = self._state()

        async def fetch(token: str | None) -> dict[str, Any]:
            params: dict[str, Any] = {"MaxResults": _PAGE}
            if name_prefix:
                params["QueueNamePrefix"] = name_prefix
            if token:
                params["NextToken"] = token
            return dict(await self._client.call("sqs", "ListQueues", **params))

        urls, next_cursor = await self._paged(
            fetch, lambda r: list(r.get("QueueUrls", [])), limit=limit, cursor=cursor, source="sqs"
        )
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for url in urls:
            item, citation = mappers.map_queue(
                url, default_region=self._client.region, citation_ref=len(citations)
            )
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.SQS, items, citations, started=call.started,
            query_echo={"name_prefix": name_prefix, "limit": limit}, next_cursor=next_cursor,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"queue SQS khớp '{name_prefix or '*'}'")

    # -- sqs_get_queue_attributes --------------------------------------------------------

    async def get_queue_attributes(
        self,
        *,
        queue_name: str | None = None,
        queue_url: str | None = None,
        include_tags: bool = False,
    ) -> ToolOutcome:
        call = self._state()
        echo: dict[str, Any] = {
            k: v for k, v in (("queue_name", queue_name), ("queue_url", queue_url)) if v
        }
        echo["include_tags"] = include_tags
        name = self._label(queue_name, queue_url)
        try:
            name, url = await self._resolve_queue(queue_name, queue_url)
            response = await self._client.call(
                "sqs", "GetQueueAttributes", QueueUrl=url, AttributeNames=["All"]
            )
            tags: dict[str, str] | None = None
            if include_tags:
                tagged = await self._client.call("sqs", "ListQueueTags", QueueUrl=url)
                tags = {str(k): call.plain(str(v)) for k, v in (tagged.get("Tags") or {}).items()}
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(SourceType.SQS, call, echo, f"Queue '{name}'")
            raise
        item, citation = mappers.map_queue_attributes(
            url, dict(response.get("Attributes", {})), default_region=self._client.region,
            tags=tags, citation_ref=0,
        )  # fmt: skip
        result = build_result(
            SourceType.SQS, [item], [citation], started=call.started, query_echo=echo,
            warnings=[_APPROX_WARNING, *call.warnings], redactions=call.counter.count,
        )  # fmt: skip
        return ToolOutcome(result, identifier=f"Queue '{name}'")

    # -- sqs_list_dead_letter_source_queues ----------------------------------------------

    async def list_dead_letter_source_queues(
        self,
        *,
        queue_name: str | None = None,
        queue_url: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolOutcome:
        self._limit(limit, "sqs")
        call = self._state()
        echo = {k: v for k, v in (("queue_name", queue_name), ("queue_url", queue_url)) if v}
        name = self._label(queue_name, queue_url)
        try:
            name, url = await self._resolve_queue(queue_name, queue_url)

            async def fetch(token: str | None) -> dict[str, Any]:
                params: dict[str, Any] = {"QueueUrl": url, "MaxResults": _PAGE}
                if token:
                    params["NextToken"] = token
                return dict(await self._client.call("sqs", "ListDeadLetterSourceQueues", **params))

            urls, next_cursor = await self._paged(
                fetch, lambda r: list(r.get("queueUrls", [])), limit=limit, cursor=cursor,
                source="sqs",
            )  # fmt: skip
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(SourceType.SQS, call, echo, f"Queue '{name}'")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for source_url in urls:
            item, citation = mappers.map_queue(
                source_url, default_region=self._client.region, citation_ref=len(citations)
            )
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.SQS, items, citations, started=call.started, query_echo=echo,
            next_cursor=next_cursor,
        )  # fmt: skip
        return ToolOutcome(
            result, query_description=f"queue nào dùng '{name}' làm dead-letter queue"
        )

    # -- sns_list_topics -----------------------------------------------------------------

    async def list_topics(
        self, *, name_prefix: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        self._limit(limit, "sns")
        self._prefix(name_prefix, 256, "sns")
        call = self._state()

        async def fetch(token: str | None) -> dict[str, Any]:
            return dict(
                await self._client.call(
                    "sns", "ListTopics", **({"NextToken": token} if token else {})
                )
            )

        def take(response: dict[str, Any]) -> list[str]:
            arns = [str(t["TopicArn"]) for t in response.get("Topics", [])]
            if name_prefix:
                return [a for a in arns if mappers.arn_name(a).startswith(name_prefix)]
            return arns

        arns, next_cursor = await self._paged(fetch, take, limit=limit, cursor=cursor, source="sns")
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        for arn in arns:
            item, citation = mappers.map_topic(arn, citation_ref=len(citations))
            items.append(item)
            citations.append(citation)
        result = build_result(
            SourceType.SNS, items, citations, started=call.started,
            query_echo={"name_prefix": name_prefix, "limit": limit}, next_cursor=next_cursor,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"topic SNS khớp '{name_prefix or '*'}'")

    # -- sns_get_topic_attributes ----------------------------------------------------------

    @staticmethod
    def _topic_arn(topic_arn: str) -> None:
        if len(topic_arn) > 512 or not _TOPIC_ARN.match(topic_arn):  # noqa: PLR2004
            raise invalid_input("topic_arn", "phải là ARN SNS (arn:aws...:sns:...)", "sns")

    async def get_topic_attributes(self, *, topic_arn: str) -> ToolOutcome:
        self._topic_arn(topic_arn)
        call = self._state()
        echo = {"topic_arn": topic_arn}
        try:
            response = await self._client.call("sns", "GetTopicAttributes", TopicArn=topic_arn)
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(SourceType.SNS, call, echo, f"Topic '{topic_arn}'")
            raise
        item, citation = mappers.map_topic_attributes(
            topic_arn, dict(response.get("Attributes", {})), citation_ref=0
        )
        result = build_result(
            SourceType.SNS, [item], [citation], started=call.started, query_echo=echo
        )
        return ToolOutcome(result, identifier=f"Topic '{topic_arn}'")

    # -- sns_list_subscriptions_by_topic ---------------------------------------------------

    async def list_subscriptions_by_topic(
        self, *, topic_arn: str, limit: int = 20, cursor: str | None = None
    ) -> ToolOutcome:
        self._topic_arn(topic_arn)
        self._limit(limit, "sns")
        call = self._state()
        echo = {"topic_arn": topic_arn}

        async def fetch(token: str | None) -> dict[str, Any]:
            params: dict[str, Any] = {"TopicArn": topic_arn}
            if token:
                params["NextToken"] = token
            return dict(await self._client.call("sns", "ListSubscriptionsByTopic", **params))

        try:
            raws, next_cursor = await self._paged(
                fetch, lambda r: list(r.get("Subscriptions", [])), limit=limit, cursor=cursor,
                source="sns",
            )  # fmt: skip
        except ToolError as exc:
            if _is_not_found(exc):
                return self._not_found(SourceType.SNS, call, echo, f"Topic '{topic_arn}'")
            raise
        items: list[dict[str, Any]] = []
        citations: list[Citation] = []
        masked_any = False
        for raw in raws:
            item, citation, masked = mappers.map_subscription(
                raw, topic_arn=topic_arn, citation_ref=len(citations)
            )
            masked_any = masked_any or masked
            items.append(item)
            citations.append(citation)
        warnings = (
            ["endpoint email/sms/http đã được che bớt (PII, credential)"] if masked_any else []
        )
        result = build_result(
            SourceType.SNS, items, citations, started=call.started, query_echo=echo,
            next_cursor=next_cursor, warnings=warnings,
        )  # fmt: skip
        return ToolOutcome(result, query_description=f"subscription của topic '{topic_arn}'")
