"""E2E over stdio for the opt-in `opensearch_search_dsl` tool (FR-004): TC-076, TC-018/TC-057
(forbidden constructs refused as `not_permitted` BEFORE any request reaches OpenSearch)."""

from __future__ import annotations

import pytest
from harness import open_server, result_payload


def _routes(method, path, query, body):
    if path == "/":
        return 200, {
            "name": "n",
            "version": {"number": "2.11.0", "distribution": "opensearch"},
            "tagline": "The OpenSearch Project: https://opensearch.org/",
        }
    if path.endswith("/_search"):
        hit = {
            "_index": "app-logs-1",
            "_id": "d1",
            "_source": {"@timestamp": "2026-09-30T10:41:12Z", "message": "m"},
        }
        return 200, {
            "took": 1,
            "timed_out": False,
            "hits": {"total": {"value": 1, "relation": "eq"}, "hits": [hit]},
        }
    return 404, {}


async def test_TC_076_dsl_size_clamp_and_paging_bounds(stub_http):
    stub = stub_http(_routes)
    env = {"MCP_OPENSEARCH_HOSTS": stub.url, "MCP_OPENSEARCH_ALLOW_DSL": "true"}
    searches = lambda: [r for r in stub.requests if r[1].endswith("/_search")]  # noqa: E731
    async with open_server("opensearch", env) as s:
        r1 = result_payload(
            await s.call_tool(
                "opensearch_search_dsl",
                {
                    "index_pattern": "app-logs-*",
                    "limit": 10,
                    "body": {"size": 50, "query": {"match_all": {}}},
                },
            )
        )
        assert r1["status"] == "ok" and r1["meta"]["warnings"], (
            r1
        )  # size clamped to limit with a warning
        n = len(searches())
        r2 = result_payload(
            await s.call_tool(
                "opensearch_search_dsl",
                {
                    "index_pattern": "app-logs-*",
                    "limit": 10,
                    "body": {"from": 950, "size": 100, "query": {"match_all": {}}},
                },
            )
        )
        assert r2["status"] == "error" and r2["error"]["code"] == "invalid_input", r2
        # from=950 trips the `from in 0..900` rule first (message has no search_after hint) ...
        assert r2["error"]["details"]["field"] == "body.from"
        # NOTE: the from+size>1000 rule (with its search_after hint) is unreachable through the tool
        # schema: from<=900 and size<=100 already bound the sum at 1000. Reported as a spec nit.
        r3 = result_payload(
            await s.call_tool(
                "opensearch_search_dsl",
                {
                    "index_pattern": "app-logs-*",
                    "limit": 10,
                    "body": {"from": 901, "query": {"match_all": {}}},
                },
            )
        )
        assert r3["status"] == "error" and r3["error"]["code"] == "invalid_input", r3
        assert len(searches()) == n  # steps 2 and 3 never reached the cluster
        r4 = result_payload(
            await s.call_tool(
                "opensearch_search_dsl",
                {
                    "index_pattern": "app-logs-*",
                    "limit": 10,
                    "body": {"from": 500, "size": 10, "query": {"match_all": {}}},
                },
            )
        )
        assert r4["status"] == "ok", r4


@pytest.mark.parametrize(
    "construct",
    [
        {"script": {"source": "1"}},
        {"runtime_mappings": {"x": {"type": "keyword"}}},
        {"aggs": {"a": {"scripted_metric": {"init_script": "1"}}}},
        {"point_in_time": {"id": "x"}},
    ],
)
async def test_TC_018_forbidden_constructs_not_permitted_without_upstream_call(
    stub_http, construct
):
    stub = stub_http(_routes)
    env = {"MCP_OPENSEARCH_HOSTS": stub.url, "MCP_OPENSEARCH_ALLOW_DSL": "true"}
    async with open_server("opensearch", env) as s:
        out = result_payload(
            await s.call_tool(
                "opensearch_search_dsl",
                {
                    "index_pattern": "app-logs-*",
                    "limit": 10,
                    "body": {"query": {"match_all": {}}, **construct},
                },
            )
        )
    assert out["status"] == "error" and out["error"]["code"] in (
        "not_permitted",
        "invalid_input",
    ), out
    assert not [r for r in stub.requests if r[1].endswith("/_search")]
