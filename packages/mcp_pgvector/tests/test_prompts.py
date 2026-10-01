"""T-065 / TC-052, TC-053: the `semantic_synthesis` prompt (FR-013/AC-001, FR-013/AC-002)."""

from __future__ import annotations

import re

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_common.config import CommonSettings
from mcp_common.contract_testing import load_contract, operations_by_id
from mcp_pgvector.prompts import SEMANTIC_SYNTHESIS_TEMPLATE
from mcp_pgvector.server import build_server
from pg_helpers import FakeClient, make_api

CONTRACT = load_contract()


async def _render(
    common: CommonSettings, question: str = "Tại sao payment retry nhiều lần?"
) -> str:
    server = build_server(make_api(FakeClient(), common))
    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_prompts()
        assert [p.name for p in listed.prompts] == ["semantic_synthesis"]
        got = await session.get_prompt("semantic_synthesis", {"question": question})
    return "\n".join(m.content.text for m in got.messages)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_prompts_list_exposes_semantic_synthesis_with_one_question_argument(
    common: CommonSettings,
) -> None:
    server = build_server(make_api(FakeClient(), common))
    async with create_connected_server_and_client_session(server) as session:
        prompt = (await session.list_prompts()).prompts[0]
    assert prompt.name == "semantic_synthesis"
    assert [a.name for a in prompt.arguments or []] == ["question"]
    assert prompt.arguments and prompt.arguments[0].required is True


@pytest.mark.asyncio
async def test_FR_013_AC_001_the_prompt_demands_the_original_source_uri(
    common: CommonSettings,
) -> None:
    text = await _render(common)
    assert "Tại sao payment retry nhiều lần?" in text
    assert "kb_semantic_search" in text and "source_uri" in text
    assert "NGUỒN GỐC" in text and "KHÔNG nêu document_id" in text
    assert "ARN" in text  # queue/topic citations
    assert "sqs_get_queue_attributes" in text  # optional step 2


@pytest.mark.asyncio
async def test_FR_013_AC_002_the_prompt_demands_an_explicit_no_indexed_data_statement(
    common: CommonSettings,
) -> None:
    text = await _render(common)
    assert "không tìm thấy dữ liệu đã index" in text
    assert "không bịa" in text
    assert "bộ lọc" in text  # filter exclusion is not "no data"


@pytest.mark.asyncio
async def test_NFR_004_the_prompt_asks_for_freshness_from_data_freshness(
    common: CommonSettings,
) -> None:
    text = await _render(common)
    assert "data_freshness" in text and "staleness_hours" in text and "last_ingested_at" in text


@pytest.mark.asyncio
async def test_the_prompt_treats_untrusted_content_as_data(common: CommonSettings) -> None:
    assert "untrusted-content" in await _render(common)


def test_every_tool_the_prompt_names_exists_in_the_contract() -> None:
    operations = operations_by_id(CONTRACT)
    named = set(re.findall(r"\b((?:kb|sqs|sns)_[a-z_]+)\b", SEMANTIC_SYNTHESIS_TEMPLATE))
    assert named >= {"kb_semantic_search", "kb_get_document", "kb_list_sources",
                     "sqs_get_queue_attributes"}  # fmt: skip
    assert named <= set(operations), named - set(operations)


@pytest.mark.asyncio
async def test_a_question_with_braces_does_not_break_the_template(common: CommonSettings) -> None:
    server = build_server(make_api(FakeClient(), common))
    async with create_connected_server_and_client_session(server) as session:
        got = await session.get_prompt("semantic_synthesis", {"question": 'what is {"a": 1}?'})
    assert '{"a": 1}' in got.messages[0].content.text  # type: ignore[union-attr]
