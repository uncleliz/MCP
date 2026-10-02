"""T-091: `mcp_jira.settings` flavor resolution + `mcp_jira.cli` (`serve`/`doctor`/`tools-dump`).

AC: FR-017/AC-003 (ADR-0019 flavor split; `uv run mcp-jira --help` runs).
"""

from __future__ import annotations

import json

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_jira.settings import Settings
from pydantic import SecretStr, ValidationError

from mcp_jira import cli


def test_auto_flavor_resolves_cloud_from_atlassian_net() -> None:
    s = Settings(
        base_url="https://acme.atlassian.net",
        email="x@y.z",
        token=SecretStr("t"),
    )
    assert s.resolved_flavor == "cloud"
    assert s.api_version == "3"


def test_auto_flavor_resolves_server_from_self_hosted() -> None:
    s = Settings(base_url="https://jira.acme.example", token=SecretStr("t"))
    assert s.resolved_flavor == "server"
    assert s.api_version == "2"


def test_explicit_flavor_overrides_url() -> None:
    s = Settings(base_url="https://jira.acme.example", flavor="cloud", email="x@y.z",
                 token=SecretStr("t"))
    assert s.resolved_flavor == "cloud" and s.api_version == "3"


def test_cloud_requires_email() -> None:
    with pytest.raises(ValidationError):
        Settings(base_url="https://acme.atlassian.net", token=SecretStr("t"))


def test_server_does_not_require_email() -> None:
    s = Settings(base_url="https://jira.acme.example", token=SecretStr("t"))
    assert s.email is None


def test_base_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        Settings(base_url="jira.acme.example", flavor="server", token=SecretStr("t"))


def test_base_url_trailing_slash_stripped() -> None:
    s = Settings(base_url="https://jira.acme.example/", flavor="server", token=SecretStr("t"))
    assert s.base_url == "https://jira.acme.example"


def test_load_settings_missing_env_names_the_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BASE_URL", "TOKEN", "EMAIL", "FLAVOR"):
        monkeypatch.delenv(f"MCP_JIRA_{var}", raising=False)
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="jira")
    assert "MCP_JIRA_BASE_URL" in exc.value.missing_vars


def test_cli_tools_dump_prints_five_tools(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["tools-dump"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(out) == [
        "jira_get_issue",
        "jira_get_sprint",
        "jira_list_board_sprints",
        "jira_list_projects",
        "jira_search_issues",
    ]


def test_cli_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "mcp-jira" in capsys.readouterr().out


def test_cli_doctor_reports_config_error_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for var in ("BASE_URL", "TOKEN", "EMAIL", "FLAVOR"):
        monkeypatch.delenv(f"MCP_JIRA_{var}", raising=False)
    rc = cli.main(["doctor"])
    assert rc == 2
    assert "config error" in capsys.readouterr().err
