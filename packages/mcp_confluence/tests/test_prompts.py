"""T-021: prompt `dev_knowledge_lookup` + server instructions (FR-003, ADR-0014)."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_confluence.prompts import SERVER_INSTRUCTIONS
from mcp_confluence.server import build_server


@pytest.fixture
def server(read_api):
    return build_server(read_api)


@pytest.mark.asyncio
async def test_FR_003_AC_001_prompts_list_has_dev_knowledge_lookup_with_question(server) -> None:
    async with create_connected_server_and_client_session(server) as session:
        prompts = (await session.list_prompts()).prompts
    assert [p.name for p in prompts] == ["dev_knowledge_lookup"]
    assert [(a.name, a.required) for a in prompts[0].arguments or []] == [("question", True)]


@pytest.mark.asyncio
async def test_FR_003_AC_001_prompt_asks_for_both_sources_and_a_sources_section(server) -> None:
    async with create_connected_server_and_client_session(server) as session:
        result = await session.get_prompt("dev_knowledge_lookup", {"question": "retry thế nào?"})
    text = result.messages[0].content.text
    assert "retry thế nào?" in text
    assert "confluence_search_pages" in text and "gitlab_search_code" in text
    assert "Nguồn:" in text


@pytest.mark.asyncio
async def test_FR_003_AC_002_prompt_demands_naming_the_empty_source(server) -> None:
    async with create_connected_server_and_client_session(server) as session:
        result = await session.get_prompt("dev_knowledge_lookup", {"question": "x"})
    text = result.messages[0].content.text
    assert "nêu rõ nguồn không có dữ liệu" in text
    assert "không tìm thấy tài liệu Confluence cho" in text


def test_server_instructions_remind_citation_discipline(server) -> None:
    assert "Nguồn" in SERVER_INSTRUCTIONS and "empty" in SERVER_INSTRUCTIONS
    assert server.instructions == SERVER_INSTRUCTIONS
