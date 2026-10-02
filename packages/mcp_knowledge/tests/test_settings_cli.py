"""T-099/T-102/T-103: settings, CLI (serve/doctor/tools-dump), and the prompt."""

from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_knowledge.cli import main
from mcp_knowledge.prompts import COMPANY_KNOWLEDGE_LOOKUP_TEMPLATE
from mcp_knowledge.server import build_server
from mcp_knowledge.settings import Settings


def test_settings_require_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_KNOWLEDGE_DSN", raising=False)
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="knowledge")
    assert any("MCP_KNOWLEDGE_DSN" in v for v in exc.value.missing_vars)


def test_settings_reject_empty_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_KNOWLEDGE_DSN", "   ")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="knowledge")


def test_settings_embedding_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_KNOWLEDGE_DSN", "postgresql://mcp_query_ro@h/db")
    monkeypatch.setenv("MCP_KNOWLEDGE_EMBEDDING_MODEL", "custom/model")
    settings = load_settings(Settings, source="knowledge")
    assert settings.embedding_settings().model == "custom/model"
    assert settings.reranker_enabled is True


def test_tools_dump_prints_eight_tools(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools-dump"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 8 and "search_company_knowledge" in out


def test_doctor_exits_two_without_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MCP_KNOWLEDGE_DSN", raising=False)
    assert main(["doctor"]) == 2


def test_serve_exits_two_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_KNOWLEDGE_DSN", raising=False)
    assert main([]) == 2  # default command is serve


def test_cli_has_the_three_commands() -> None:
    import mcp_knowledge.cli as cli_module

    source = __import__("pathlib").Path(cli_module.__file__).read_text(encoding="utf-8")
    for command in ("doctor", "serve", "tools-dump"):
        assert f'"{command}"' in source


@pytest.mark.asyncio
async def test_prompt_is_registered_and_enforces_unknown() -> None:
    async with create_connected_server_and_client_session(build_server()) as session:
        prompts = {p.name for p in (await session.list_prompts()).prompts}
    assert prompts == {"company_knowledge_lookup"}
    assert "Tôi không tìm thấy nguồn chính thức xác nhận thông tin này." in (
        COMPANY_KNOWLEDGE_LOOKUP_TEMPLATE
    )
    assert "insufficient_evidence" in COMPANY_KNOWLEDGE_LOOKUP_TEMPLATE
    assert "CONFLICT" in COMPANY_KNOWLEDGE_LOOKUP_TEMPLATE
