"""E2E journeys (FR-003, FR-009, FR-013): TC-014/015, TC-033/034, TC-052/053.

A real MCP client (the official SDK) talks to real stdio server subprocesses. The "LLM step" of a
journey is out of reach in CI (no model), so the scripted client performs exactly what the prompt
instructs a model to do: `prompts/get`, then the tool calls the prompt names (verifying that every
named tool is really exposed by one of the servers), and the test asserts what the *platform*
controls: each source answers with `ok` + resolvable citation of its own source type, or an
explicit `empty` statement, and the prompt itself carries the mandatory "state the gap / cite each
source" rules. The wording of a model-written final answer is covered by eval/questions.yaml
(TC-065, manual).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import boto3
import httpx
import pytest
from harness import StubHttp, load_fixture, open_server, result_payload
from moto.server import ThreadedMotoServer

# -- Journey 1: Confluence + GitLab (TC-014, TC-015) ---------------------------------------------


def _atlassian_routes(confluence_empty: bool):
    def routes(method: str, path: str, query: dict, body: bytes):
        if path.endswith("/rest/api/content/search"):
            name = "search_empty.json" if confluence_empty else "search_results.json"
            return 200, load_fixture("mcp_confluence", f"confluence/{name}")
        if path == "/api/v4/search":
            return 200, load_fixture("mcp_gitlab", "gitlab/search_blobs.json")
        if re.fullmatch(r"/api/v4/projects/\d+", path):
            return 200, load_fixture("mcp_gitlab", "gitlab/project_42.json")
        return 404, {"message": "404 Not Found"}

    return routes


async def _journey1(stub_http: Callable[..., StubHttp], confluence_empty: bool):
    stub = stub_http(_atlassian_routes(confluence_empty))
    async with (
        open_server("confluence", {"MCP_CONFLUENCE_BASE_URL": stub.url}) as conf,
        open_server("gitlab", {"MCP_GITLAB_BASE_URL": stub.url}) as gl,
    ):
        prompt = await conf.get_prompt(
            "dev_knowledge_lookup", {"question": "How does payment retry work?"}
        )
        prompt_text = prompt.messages[0].content.text
        tool_names = {t.name for t in (await conf.list_tools()).tools} | {
            t.name for t in (await gl.list_tools()).tools
        }
        for named in ("confluence_search_pages", "gitlab_search_code"):
            assert named in prompt_text and named in tool_names
        c = await conf.call_tool("confluence_search_pages", {"query": "payment retry"})
        g = await gl.call_tool("gitlab_search_code", {"query": "retry"})
    return prompt_text, result_payload(c), result_payload(g), stub


async def test_TC_014_dev_knowledge_lookup_cites_both_sources(stub_http):
    prompt_text, conf, gl, stub = await _journey1(stub_http, confluence_empty=False)
    assert conf["status"] == "ok" and gl["status"] == "ok"
    conf_uris = [c["uri"] for c in conf["citations"] if c["source_type"] == "confluence"]
    gl_uris = [c["uri"] for c in gl["citations"] if c["source_type"] == "gitlab"]
    assert conf_uris and all(u.startswith("http") for u in conf_uris)
    assert gl_uris and all("/api/v4" not in u for u in gl_uris)  # web URL, never the API URL
    assert 'mục "Nguồn:"' in prompt_text
    assert {m for m, _ in stub.requests} == {"GET"}


async def test_TC_015_empty_confluence_is_stated_not_fabricated(stub_http):
    prompt_text, conf, gl, _ = await _journey1(stub_http, confluence_empty=True)
    assert conf["status"] == "empty" and conf["citations"] == [] and conf["items"] == []
    assert gl["status"] == "ok" and gl["citations"]  # GitLab citation still present
    assert "không tìm thấy tài liệu Confluence" in prompt_text  # mandatory gap statement in prompt


# -- Journey 2: CloudWatch + OpenSearch + Kibana (TC-033, TC-034) --------------------------------

TF, TT = "2026-09-30T10:00:00Z", "2026-09-30T12:00:00Z"


def _os_kibana_routes(os_empty: bool = False):
    def routes(method: str, path: str, query: dict, body: bytes):
        if path == "/" and method == "GET":
            return 200, {
                "name": "n",
                "version": {"number": "2.11.0", "distribution": "opensearch"},
                "tagline": "The OpenSearch Project: https://opensearch.org/",
            }
        if path.endswith("/_search"):
            hits = (
                []
                if os_empty
                else [
                    {
                        "_index": "app-logs-2026.09.30",
                        "_id": "doc-1",
                        "_score": None,
                        "_source": {
                            "@timestamp": "2026-09-30T10:41:12Z",
                            "level": "ERROR",
                            "service": "payment",
                            "message": "payment gateway timeout after 3 retries",
                        },
                        "sort": [1790764872000, "doc-1"],
                    }
                ]
            )
            return 200, {
                "took": 3,
                "timed_out": False,
                "hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": hits},
            }
        if path.endswith("/api/saved_objects/_find"):
            return 200, {
                "page": 1,
                "per_page": 20,
                "total": 1,
                "saved_objects": [
                    {
                        "type": "dashboard",
                        "id": "dash-1",
                        "updated_at": "2026-09-12T02:00:00.000Z",
                        "attributes": {
                            "title": "Payment Service Overview",
                            "description": "Latency",
                        },
                        "references": [],
                    }
                ],
            }
        if "/api/saved_objects/dashboard/dash-1" in path:
            return 200, {
                "type": "dashboard",
                "id": "dash-1",
                "updated_at": "2026-09-12T02:00:00.000Z",
                "attributes": {"title": "Payment Service Overview", "description": "d"},
                "references": [],
            }
        return 404, {"statusCode": 404, "error": "Not Found", "message": "nope"}

    return routes


@pytest.fixture
def moto_url():
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    url = f"http://{host}:{port}"
    httpx.post(f"{url}/moto-api/reset")  # moto state is process-global: start every test clean
    yield url
    server.stop()


async def _journey2(stub_http, moto_url: str, with_alarm: bool):
    if with_alarm:
        cw = boto3.client(
            "cloudwatch",
            region_name="us-east-1",
            endpoint_url=moto_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        cw.put_metric_alarm(
            AlarmName="payment-5xx-high",
            MetricName="5XXError",
            Namespace="App",
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=1.0,
            ComparisonOperator="GreaterThanThreshold",
        )
    stub = stub_http(_os_kibana_routes())
    async with (
        open_server("cloudwatch", {"MCP_CLOUDWATCH_ENDPOINT_URL": moto_url}) as cwx,
        open_server("opensearch", {"MCP_OPENSEARCH_HOSTS": stub.url}) as osx,
        open_server("kibana", {"MCP_KIBANA_BASE_URL": stub.url}) as kbx,
    ):
        prompt = await cwx.get_prompt(
            "incident_investigation", {"service": "payment", "time_from": TF, "time_to": TT}
        )
        text = prompt.messages[0].content.text
        exposed = {t.name for s in (cwx, osx, kbx) for t in (await s.list_tools()).tools}
        for tool in re.findall(r"\b(?:cloudwatch|opensearch|kibana)_[a-z_]+", text):
            assert tool in exposed, f"prompt names unknown tool {tool}"
        alarms = result_payload(await cwx.call_tool("cloudwatch_describe_alarms", {}))
        logs = result_payload(
            await osx.call_tool(
                "opensearch_search_logs",
                {
                    "index_pattern": "app-logs-*",
                    "query": "service:payment",
                    "time_from": TF,
                    "time_to": TT,
                },
            )
        )
        found = result_payload(
            await kbx.call_tool("kibana_find_saved_objects", {"query": "payment"})
        )
        link = result_payload(
            await kbx.call_tool(
                "kibana_build_dashboard_link",
                {"dashboard_id": "dash-1", "time_from": TF, "time_to": TT},
            )
        )
    return text, alarms, logs, found, link


async def test_TC_033_incident_investigation_cites_each_source(stub_http, moto_url):
    text, alarms, logs, found, link = await _journey2(stub_http, moto_url, with_alarm=True)
    assert alarms["status"] == "ok" and alarms["citations"][0]["source_type"] == "cloudwatch"
    assert "payment-5xx-high" in str(alarms["citations"])
    assert logs["status"] == "ok" and logs["citations"][0]["source_type"] == "opensearch"
    loc = logs["citations"][0]["locator"]
    assert "doc-1" in str(loc) and "app-logs-2026.09.30" in str(loc)
    assert found["status"] == "ok" and link["status"] == "ok"
    assert link["citations"][0]["source_type"] == "kibana"
    assert "dash-1" in link["citations"][0]["uri"] and "_g=" in link["citations"][0]["uri"]


async def test_TC_034_empty_cloudwatch_alarms_stated_as_gap(stub_http, moto_url):
    text, alarms, logs, found, link = await _journey2(stub_http, moto_url, with_alarm=False)
    assert alarms["status"] == "empty" and alarms["citations"] == []
    assert logs["status"] == "ok" and link["status"] == "ok"  # other sources unaffected
    assert "Không có alarm CloudWatch trong khung giờ này" in text  # mandatory gap sentence


# -- Journey 3 (pgvector + SQS) lives in test_pgvector_ingest_e2e.py (needs Postgres) ------------
