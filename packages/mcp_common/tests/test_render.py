"""T-009: mcp_common.render — golden-file snapshots for all 4 status branches ×
(list-tool, detail-tool), per `info.x-text-rendering` in api-contract.yaml.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp_common.content import wrap_untrusted
from mcp_common.envelope import Citation, DataFreshness, Meta, ResultStatus, SourceType, ToolResult
from mcp_common.render import render_text

GOLDEN_DIR = Path(__file__).parent / "golden"

_AS_OF = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)


def _meta(**overrides: object) -> Meta:
    base: dict[str, object] = {
        "source": SourceType.CONFLUENCE,
        "returned": 0,
        "has_more": False,
        "truncated": False,
        "elapsed_ms": 42,
        "as_of": _AS_OF,
    }
    base.update(overrides)
    return Meta(**base)  # type: ignore[arg-type]


def _assert_matches_golden(name: str, actual: str) -> None:
    golden_path = GOLDEN_DIR / name
    expected = golden_path.read_text(encoding="utf-8")
    message = f"{name} mismatch.\n--- actual ---\n{actual}\n--- expected ---\n{expected}"
    assert actual == expected, message


# ---------------------------------------------------------------------------
# list-tool fixture: confluence_search_pages-shaped, 2 items
# ---------------------------------------------------------------------------


def _list_tool_result(status: ResultStatus, *, truncated: bool = False) -> ToolResult:
    items = [
        {
            "citation_ref": 0,
            "title": "Payment retry policy",
            "content": wrap_untrusted(
                "Retry 3 times with exponential backoff.", source="confluence", content_id="111"
            ),
        },
        {
            "citation_ref": 1,
            "title": "Payment gateway runbook",
            "content": wrap_untrusted(
                "Escalate to on-call if failure rate > 5%.", source="confluence", content_id="222"
            ),
        },
    ]
    citations = [
        Citation(
            source_type=SourceType.CONFLUENCE,
            label="Payment retry policy (PAY) v7",
            uri="https://example.atlassian.net/wiki/spaces/PAY/pages/111",
            locator={"page_id": "111", "version": 7},
        ),
        Citation(
            source_type=SourceType.CONFLUENCE,
            label="Payment gateway runbook (PAY) v3",
            uri="https://example.atlassian.net/wiki/spaces/PAY/pages/222",
            locator={"page_id": "222", "version": 3},
        ),
    ]
    warnings = ["max_bytes bị kẹp xuống 131072"] if truncated else []
    redactions = 1 if truncated else 0
    return ToolResult(
        status=status,
        items=items,
        citations=citations,
        meta=_meta(returned=2, truncated=truncated, warnings=warnings, redactions=redactions),
    )


def test_ok_list_tool_matches_golden() -> None:
    result = _list_tool_result(ResultStatus.OK)
    _assert_matches_golden("ok_list.txt", render_text(result))


def test_partial_list_tool_matches_golden() -> None:
    result = _list_tool_result(ResultStatus.PARTIAL, truncated=True)
    _assert_matches_golden("partial_list.txt", render_text(result))


# ---------------------------------------------------------------------------
# detail-tool fixture: confluence_get_page-shaped, 1 item
# ---------------------------------------------------------------------------


def _detail_tool_result(status: ResultStatus) -> ToolResult:
    items = [
        {
            "citation_ref": 0,
            "title": "Payment retry policy",
            "content": wrap_untrusted(
                "Full page body: retry 3 times with exponential backoff.",
                source="confluence",
                content_id="111",
            ),
        },
    ]
    citations = [
        Citation(
            source_type=SourceType.CONFLUENCE,
            label="Payment retry policy (PAY) v7",
            uri="https://example.atlassian.net/wiki/spaces/PAY/pages/111",
            locator={"page_id": "111", "version": 7},
        ),
    ]
    return ToolResult(status=status, items=items, citations=citations, meta=_meta(returned=1))


def test_ok_detail_tool_matches_golden() -> None:
    result = _detail_tool_result(ResultStatus.OK)
    _assert_matches_golden("ok_detail.txt", render_text(result))


def test_partial_detail_tool_matches_golden() -> None:
    # A partial detail result: got the page but it was cut off mid-body.
    items = [
        {
            "citation_ref": 0,
            "title": "Payment retry policy",
            "content": wrap_untrusted(
                "Full page body: retry 3 times...", source="confluence", content_id="111"
            ),
        },
    ]
    citations = [
        Citation(
            source_type=SourceType.CONFLUENCE,
            label="Payment retry policy (PAY) v7",
            uri="https://example.atlassian.net/wiki/spaces/PAY/pages/111",
            locator={"page_id": "111", "version": 7},
        ),
    ]
    result = ToolResult(
        status=ResultStatus.PARTIAL,
        items=items,
        citations=citations,
        meta=_meta(returned=1, truncated=True, warnings=["nội dung vượt max_bytes, đã cắt"]),
    )
    _assert_matches_golden("partial_detail.txt", render_text(result))


# ---------------------------------------------------------------------------
# empty / not_found — identical regardless of list-tool vs detail-tool (no items)
# ---------------------------------------------------------------------------


def test_empty_matches_golden_exact_contract_sentence() -> None:
    result = ToolResult(
        status=ResultStatus.EMPTY,
        items=[],
        citations=[],
        meta=_meta(query_echo={"query": "nonexistent-xyz"}),
    )
    description = 'tài liệu Confluence cho "nonexistent-xyz"'
    text = render_text(result, query_description=description)
    _assert_matches_golden("empty.txt", text)
    expected_sentence = 'Không tìm thấy tài liệu Confluence cho "nonexistent-xyz" ở Confluence.'
    assert text.splitlines()[0] == expected_sentence
    assert "Nguồn:" not in text


def test_not_found_matches_golden() -> None:
    result = ToolResult(
        status=ResultStatus.NOT_FOUND,
        items=[],
        citations=[],
        meta=_meta(source=SourceType.GITLAB, query_echo={"issue_iid": "9999"}),
    )
    text = render_text(result, identifier="Issue #9999")
    _assert_matches_golden("not_found.txt", text)
    assert "Nguồn:" not in text


# ---------------------------------------------------------------------------
# freshness line (mcp-pgvector only, NFR-004)
# ---------------------------------------------------------------------------


def test_data_freshness_line_is_rendered_when_present() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0, "title": "chunk"}],
        citations=[
            Citation(source_type=SourceType.PGVECTOR, label="Original doc", uri="https://x")
        ],
        meta=_meta(
            source=SourceType.PGVECTOR,
            returned=1,
            data_freshness=DataFreshness(
                last_ingested_at=_AS_OF, staleness_hours=2.5, embedding_model="bge-m3"
            ),
        ),
    )
    text = render_text(result)
    assert "Độ mới dữ liệu" in text
    assert "2.5" in text


@pytest.mark.parametrize(
    "name",
    [
        "ok_list.txt",
        "partial_list.txt",
        "ok_detail.txt",
        "partial_detail.txt",
        "empty.txt",
        "not_found.txt",
    ],
)
def test_golden_file_exists(name: str) -> None:
    assert (GOLDEN_DIR / name).is_file()
