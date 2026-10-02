"""T-091: cover `mcp_jira.cli` serve/doctor happy + failure paths without real stdio or network
(the `serve` handoff and the credential check are both monkeypatched)."""

from __future__ import annotations

import pytest

from mcp_jira import cli


@pytest.fixture(autouse=True)
def _jira_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_JIRA_BASE_URL", "https://jira.acme.example")
    monkeypatch.setenv("MCP_JIRA_FLAVOR", "server")
    monkeypatch.setenv("MCP_JIRA_TOKEN", "pat-not-a-real-secret")
    monkeypatch.delenv("MCP_JIRA_EMAIL", raising=False)


def test_serve_invokes_runtime_serve_and_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, object] = {}

    async def fake_serve(build_fn, *, server_name, credential_check, settings):
        served["server_name"] = server_name
        served["has_check"] = credential_check is not None
        build_fn()  # exercise the lazy build path

    monkeypatch.setattr(cli, "serve", fake_serve)
    assert cli.main(["serve"]) == 0
    assert served == {"server_name": "mcp-jira", "has_check": True}


def test_serve_is_the_default_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_serve(build_fn, *, server_name, credential_check, settings):
        calls.append(server_name)

    monkeypatch.setattr(cli, "serve", fake_serve)
    assert cli.main([]) == 0
    assert calls == ["mcp-jira"]


def test_doctor_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from mcp_jira.client import CredentialReport

    async def fake_verify(self):  # noqa: ANN001
        return CredentialReport(True, [])

    monkeypatch.setattr("mcp_jira.client.JiraClient.verify_credentials", fake_verify)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "config: ok" in out and "flavor=server" in out and "ok" in out


def test_doctor_reports_failure_reasons(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mcp_jira.client import CredentialReport

    async def fake_verify(self):  # noqa: ANN001
        return CredentialReport(False, ["account is not read-only"])

    monkeypatch.setattr("mcp_jira.client.JiraClient.verify_credentials", fake_verify)
    assert cli.main(["doctor"]) == 1
    assert "account is not read-only" in capsys.readouterr().out
