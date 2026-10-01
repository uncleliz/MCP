"""A fake `AsyncOpenSearch` and payload builders for the mcp-opensearch tests.

Payloads are hand-written to the shape of the OpenSearch REST API docs (no live cluster is
reachable from this container); they have NOT been captured from a real cluster.
"""

from __future__ import annotations

from typing import Any

INDEX = "app-logs-2026.09.30"

ARGS_OK_SEARCH = {
    "index_pattern": "app-logs-*",
    "query": "service:payment AND level:ERROR",
    "time_from": "2026-09-30T10:00:00Z",
    "time_to": "2026-09-30T12:00:00Z",
}


def hit(doc_id: str, ts: str = "2026-09-30T10:41:12Z", **source: Any) -> dict[str, Any]:
    body = {
        "@timestamp": ts,
        "level": "ERROR",
        "service": "payment",
        "message": "payment gateway timeout after 3 retries",
        **source,
    }
    return {
        "_index": INDEX, "_id": doc_id, "_score": None, "_source": body,
        "sort": [1790764872000, doc_id],
    }  # fmt: skip


def search_response(hits: list[dict[str, Any]], *, timed_out: bool = False, **extra: Any) -> dict:
    return {
        "took": 5,
        "timed_out": timed_out,
        "hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": hits},
        **extra,
    }


MAPPING_RESPONSE = {
    INDEX: {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "level": {"type": "keyword"},
                "message": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "http": {"properties": {"status": {"type": "integer"}}},
                "tags": {"type": "nested", "properties": {"name": {"type": "keyword"}}},
            }
        }
    }
}

CAT_INDICES = [
    {"index": INDEX, "health": "green", "status": "open", "docs.count": "18240122",
     "store.size": "4.2gb", "creation.date.string": "2026-09-30T00:00:00.000Z"},
    {"index": ".kibana_1", "health": "green", "status": "open", "docs.count": "12",
     "store.size": "1mb", "creation.date.string": "2026-01-01T00:00:00.000Z"},
    {"index": "app-logs-2026.09.29", "health": "yellow", "status": "open", "docs.count": None,
     "store.size": None, "creation.date.string": None},
]  # fmt: skip

AUTHINFO_RO = {"user_name": "mcp_ro", "backend_roles": [], "roles": ["readall", "own_index"]}
AUTHINFO_ADMIN = {"user_name": "admin", "backend_roles": ["admin"], "roles": ["all_access"]}


class _Namespace:
    def __init__(self, owner: FakeOpenSearch) -> None:
        self._owner = owner


class _Cat(_Namespace):
    async def indices(self, **kwargs: Any) -> Any:
        return await self._owner._record("cat.indices", kwargs)


class _Indices(_Namespace):
    async def get_mapping(self, **kwargs: Any) -> Any:
        return await self._owner._record("indices.get_mapping", kwargs)


class _Transport(_Namespace):
    async def perform_request(self, method: str, url: str, **kwargs: Any) -> Any:
        return await self._owner._record(f"transport {method} {url}", kwargs)


class FakeOpenSearch:
    """`responses[name]` is a payload, an Exception (raised), or a callable(kwargs)."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = {
            "info": {"version": {"number": "2.15.0"}},
            "transport GET /_plugins/_security/authinfo": AUTHINFO_RO,
            **(responses or {}),
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cat = _Cat(self)
        self.indices = _Indices(self)
        self.transport = _Transport(self)
        self.closed = False

    async def _record(self, name: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((name, kwargs))
        response = self.responses.get(name)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return await response(kwargs) if _is_async(response) else response(kwargs)
        return response

    async def info(self, **kwargs: Any) -> Any:
        return await self._record("info", kwargs)

    async def search(self, **kwargs: Any) -> Any:
        return await self._record("search", kwargs)

    async def count(self, **kwargs: Any) -> Any:
        return await self._record("count", kwargs)

    async def close(self) -> None:
        self.closed = True

    def last(self, name: str) -> dict[str, Any]:
        return next(kwargs for n, kwargs in reversed(self.calls) if n == name)


def _is_async(fn: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


OK_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("opensearch_list_indices", {"pattern": "app-*"}),
    ("opensearch_get_mapping", {"index": INDEX}),
    ("opensearch_search_logs", dict(ARGS_OK_SEARCH)),
    (
        "opensearch_count",
        {k: ARGS_OK_SEARCH[k] for k in ("index_pattern", "query", "time_from", "time_to")},
    ),
    (
        "opensearch_aggregate",
        {
            "index_pattern": "app-logs-*",
            "agg_type": "terms",
            "field": "error_code.keyword",
            "time_from": "2026-09-30T10:00:00Z",
            "time_to": "2026-09-30T12:00:00Z",
        },
    ),
    (
        "opensearch_search_dsl",
        {"index_pattern": "app-logs-*", "body": {"query": {"match": {"service": "payment"}}}},
    ),
]

AGG_RESPONSE = search_response(
    [], aggregations={"agg": {"buckets": [{"key": "GATEWAY_TIMEOUT", "doc_count": 1204}]}}
)


def install_ok_responses(fake: FakeOpenSearch) -> None:
    """Responses for OK_CALLS: `search` answers by request shape (aggs -> buckets)."""

    def search(kwargs: dict[str, Any]) -> dict[str, Any]:
        if "aggs" in kwargs["body"]:
            return AGG_RESPONSE
        return search_response([hit("aBcD1234"), hit("eFgH5678", "2026-09-30T10:40:00Z")])

    fake.responses["search"] = search


# Forbidden constructs (TC-018), keyed by name; shared by the client, read_api and readonly tests.
FORBIDDEN_BODIES = {
    "script": {"query": {"bool": {"filter": [{"script": {"script": "doc['x'].value > 1"}}]}}},
    "script_score": {"query": {"script_score": {"query": {"match_all": {}}, "script": {}}}},
    "script_fields": {"script_fields": {"f": {"script": "1"}}},
    "scripted_metric": {"aggs": {"a": {"scripted_metric": {"init_script": "x"}}}},
    "bucket_script": {"aggs": {"a": {"bucket_script": {"buckets_path": {}}}}},
    "bucket_selector": {"aggs": {"a": {"bucket_selector": {"buckets_path": {}}}}},
    "_script": {"sort": [{"_script": {"type": "number", "script": "1"}}]},
    "runtime_mappings": {"runtime_mappings": {"r": {"type": "keyword"}}, "query": {}},
    "id": {"query": {"template": {"id": "stored-template"}}},
    "scroll": {"query": {}, "scroll": "1m"},
    "pit": {"pit": {"id": "abc", "keep_alive": "1m"}, "query": {}},
    "point_in_time": {"point_in_time": {"id": "abc"}},
    "profile": {"profile": True, "query": {}},
    "search_template": {"search_template": {}},
    "indices_boost": {"indices_boost": [{"other": 2}]},
    "terms_lookup": {"query": {"terms": {"user": {"index": "secret-idx", "path": "ids"}}}},
    "deep_nested": {
        "aggs": {"a": {"aggs": {"b": {"filter": {"bool": {"must": [{"script": {}}]}}}}}}
    },
}  # fmt: skip
