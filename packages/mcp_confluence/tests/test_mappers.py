"""T-019: mappers — Cloud payload -> contract item schemas + Citation."""

from __future__ import annotations

from mcp_common.envelope import SourceType
from mcp_confluence.mappers import (
    map_child,
    map_page,
    map_space,
    storage_to_markdown,
    storage_to_text,
)

BASE = "https://acme.atlassian.net/wiki"


def test_FR_001_AC_001_page_citation_has_openable_url_and_page_id_version(fixture) -> None:
    raw = fixture("search_results.json")["results"][0]
    item, citation = map_page(raw, base_url=BASE, citation_ref=0, excerpt="ex")
    assert item["id"] == "123456"
    assert item["url"] == f"{BASE}/spaces/PAY/pages/123456/Payment+retry+policy"
    assert item["space_key"] == "PAY"
    assert item["version"] == 7
    assert item["updated_at"] == "2026-09-01T08:30:00.000Z"
    assert item["author"] == "Dana Nguyen"
    assert item["labels"] == ["payments", "retry"]
    assert item["excerpt"] == "ex" and item["content"] is None
    assert item["citation_ref"] == 0
    assert citation.source_type == SourceType.CONFLUENCE
    assert citation.uri == item["url"]
    assert citation.locator == {"page_id": "123456", "version": 7}
    assert "Payment retry policy" in citation.label and "PAY" in citation.label
    assert citation.retrieved_at is not None


def test_url_falls_back_to_viewpage_action_without_webui() -> None:
    raw = {"id": "42", "title": "T", "space": {"key": "K"}, "version": {"number": 1}}
    item, _ = map_page(raw, base_url=BASE, citation_ref=3)
    assert item["url"] == f"{BASE}/pages/viewpage.action?pageId=42"
    assert item["citation_ref"] == 3


def test_space_key_falls_back_to_expandable() -> None:
    raw = {"id": "42", "title": "T", "_expandable": {"space": "/rest/api/space/OPS"}}
    item, _ = map_page(raw, base_url=BASE, citation_ref=0)
    assert item["space_key"] == "OPS"


def test_links_base_from_response_wins_over_settings() -> None:
    raw = {"id": "1", "title": "T", "space": {"key": "K"}, "_links": {"webui": "/spaces/K/pages/1"}}
    item, _ = map_page(raw, base_url=BASE, links_base="https://other.example/wiki", citation_ref=0)
    assert item["url"] == "https://other.example/wiki/spaces/K/pages/1"


def test_long_label_is_capped_at_512() -> None:
    raw = {"id": "1", "title": "x" * 900, "space": {"key": "K"}}
    _, citation = map_page(raw, base_url=BASE, citation_ref=0)
    assert len(citation.label) <= 512


def test_space_mapping_and_unknown_type_becomes_null(fixture) -> None:
    spaces = fixture("spaces.json")["results"]
    pay, citation_pay = map_space(spaces[0], base_url=BASE, citation_ref=0)
    assert pay == {
        "key": "PAY",
        "name": "Payments",
        "type": "global",
        "url": f"{BASE}/spaces/PAY",
        "citation_ref": 0,
    }
    assert citation_pay.locator == {"space_key": "PAY"}
    personal, _ = map_space(spaces[1], base_url=BASE, citation_ref=1)
    assert personal["type"] == "personal"
    kb, _ = map_space(spaces[2], base_url=BASE, citation_ref=2)
    assert kb["type"] is None  # contract enum is {global, personal, null}


def test_child_mapping(fixture) -> None:
    raw = fixture("children.json")["results"][0]
    item, citation = map_child(raw, base_url=BASE, citation_ref=0)
    assert item == {
        "id": "200",
        "title": "Retry runbook",
        "url": f"{BASE}/spaces/PAY/pages/200/Retry+runbook",
        "updated_at": "2026-08-20T10:00:00.000Z",
        "citation_ref": 0,
    }
    assert citation.locator == {"page_id": "200", "version": 3}


def test_storage_to_markdown_keeps_structure(fixture) -> None:
    body = fixture("page.json")["body"]["storage"]["value"]
    md = storage_to_markdown(body)
    assert "# Retry policy" in md
    assert "**3 times**" in md
    assert "[runbook](https://acme.atlassian.net/wiki/spaces/PAY/pages/2)" in md
    assert "- attempt 1: 1m" in md
    assert "retry(max=3)" in md  # CDATA macro body survives


def test_storage_to_text_strips_markup(fixture) -> None:
    text = storage_to_text(fixture("page.json")["body"]["storage"]["value"])
    assert "<" not in text and "Retry policy" in text and "retry(max=3)" in text


def test_storage_to_markdown_inline_and_block_elements() -> None:
    md = storage_to_markdown(
        "<p><em>it</em> <code>x</code> <a>bare</a></p><pre>a = 1</pre>"
        "<table><tr><th>h</th><td>v</td></tr></table><p>l1<br/>l2</p>"
    )
    assert "*it*" in md and "`x`" in md and "[bare]" in md
    assert "```\na = 1\n```" in md and "| h | v" in md and "l1\nl2" in md
