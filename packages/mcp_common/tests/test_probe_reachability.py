"""T-001: S1 probe script classification + rendering (offline, no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe_reachability.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("probe_reachability", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_reachability"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("code", "expected"),
    [(200, "reachable"), (404, "reachable"), (401, "auth-fail"), (403, "auth-fail"),
     (502, "unreachable")],
)
def test_nfr002_classify_http(probe, code: int, expected: str) -> None:
    assert probe.classify_http(code) == expected


def test_nfr002_unresolvable_host_is_unreachable(probe) -> None:
    res = probe.probe_http("x", "https://nonexistent.invalid", "/", 1.0)
    assert res.status == "unreachable"
    assert res.dns == "fail"


def test_nfr002_unconfigured_sources_reported_not_crashed(
    probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MCP_CONFLUENCE_BASE_URL", "MCP_GITLAB_BASE_URL", "MCP_OPENSEARCH_HOSTS",
                "MCP_KIBANA_BASE_URL", "MCP_CLOUDWATCH_REGION"):
        monkeypatch.delenv(var, raising=False)
    results = [p(1.0) for p in probe.PROBES]
    assert [r.status for r in results] == ["not-configured"] * 5
    table = probe.render_markdown(results)
    assert table.count("\n") == 6
    assert probe.main(["--timeout", "1"]) == 0
