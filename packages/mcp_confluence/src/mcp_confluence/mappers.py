"""Confluence Cloud payloads -> contract item schemas (`ConfluencePage`,
`ConfluenceSpace`, `ConfluenceChildPage`) + `Citation` (FR-001/AC-001, FR-015).

Pure functions, no I/O. Free-text fields (`excerpt`, `content`) arrive here already
redacted and wrapped by `read_api.py`; this module only reshapes.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from mcp_common.content import normalize
from mcp_common.envelope import Citation, SourceType

__all__ = [
    "map_page",
    "map_space",
    "map_child",
    "storage_to_markdown",
    "storage_to_text",
]

_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_BLANK_RUNS = re.compile(r"\n{3,}")
_SPACE_TYPES = {"global", "personal"}


def _expose_cdata(storage: str) -> str:
    """Macro bodies (code, noformat...) live in CDATA, which HTMLParser drops."""
    return _CDATA.sub(lambda m: html.escape(m.group(1)), storage)


class _MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._hrefs: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self._out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "br", "tr"):
            self._out.append("\n")
        elif tag == "li":
            self._out.append("\n- ")
        elif tag in ("strong", "b"):
            self._out.append("**")
        elif tag in ("em", "i"):
            self._out.append("*")
        elif tag == "code":
            self._out.append("`")
        elif tag == "pre":
            self._out.append("\n```\n")
        elif tag == "a":
            self._hrefs.append(dict(attrs).get("href"))
            self._out.append("[")
        elif tag in ("td", "th"):
            self._out.append(" | ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self._out.append("\n")
        elif tag in ("p", "div", "tr", "ul", "ol"):
            self._out.append("\n")
        elif tag in ("strong", "b"):
            self._out.append("**")
        elif tag in ("em", "i"):
            self._out.append("*")
        elif tag == "code":
            self._out.append("`")
        elif tag == "pre":
            self._out.append("\n```\n")
        elif tag == "a":
            href = self._hrefs.pop() if self._hrefs else None
            self._out.append(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        self._out.append(data)

    def result(self) -> str:
        lines = [line.rstrip() for line in "".join(self._out).splitlines()]
        return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def storage_to_markdown(storage: str) -> str:
    parser = _MarkdownParser()
    parser.feed(_expose_cdata(storage))
    parser.close()
    return parser.result()


def storage_to_text(storage: str) -> str:
    return normalize(_expose_cdata(storage), source_format="html")


def _absolute(base: str, webui: str | None) -> str | None:
    if not webui:
        return None
    if webui.startswith(("http://", "https://")):
        return webui
    return f"{base.rstrip('/')}{webui}"


def _space_key(raw: dict[str, Any]) -> str:
    space = raw.get("space")
    if isinstance(space, dict) and space.get("key"):
        return str(space["key"])
    expandable = (raw.get("_expandable") or {}).get("space")
    if isinstance(expandable, str) and "/" in expandable:
        return expandable.rsplit("/", 1)[-1]
    return ""


def _page_url(raw: dict[str, Any], base: str) -> str:
    url = _absolute(base, (raw.get("_links") or {}).get("webui"))
    return url or f"{base.rstrip('/')}/pages/viewpage.action?pageId={raw['id']}"


def _now() -> datetime:
    return datetime.now(UTC)


def map_page(
    raw: dict[str, Any],
    *,
    base_url: str,
    citation_ref: int,
    links_base: str | None = None,
    excerpt: str | None = None,
    content: str | None = None,
) -> tuple[dict[str, Any], Citation]:
    base = links_base or base_url
    page_id = str(raw["id"])
    version_info = raw.get("version") or {}
    version = version_info.get("number")
    space_key = _space_key(raw)
    url = _page_url(raw, base)
    labels = [
        str(label["name"])
        for label in ((raw.get("metadata") or {}).get("labels") or {}).get("results", [])
        if isinstance(label, dict) and label.get("name")
    ]
    item = {
        "id": page_id,
        "title": raw.get("title", ""),
        "space_key": space_key,
        "url": url,
        "excerpt": excerpt,
        "content": content,
        "version": version,
        "updated_at": version_info.get("when"),
        "author": (version_info.get("by") or {}).get("displayName"),
        "labels": labels,
        "citation_ref": citation_ref,
    }
    label_text = f"{item['title']} ({space_key})" if space_key else str(item["title"])
    if version is not None:
        label_text += f" v{version}"
    citation = Citation(
        source_type=SourceType.CONFLUENCE,
        label=label_text[:512] or page_id,
        uri=url,
        locator={"page_id": page_id, "version": version},
        retrieved_at=_now(),
    )
    return item, citation


def map_space(
    raw: dict[str, Any], *, base_url: str, citation_ref: int, links_base: str | None = None
) -> tuple[dict[str, Any], Citation]:
    base = links_base or base_url
    key = str(raw["key"])
    url = (
        _absolute(base, (raw.get("_links") or {}).get("webui"))
        or f"{base.rstrip('/')}/spaces/{key}"
    )
    space_type = raw.get("type")
    item = {
        "key": key,
        "name": raw.get("name", key),
        "type": space_type if space_type in _SPACE_TYPES else None,
        "url": url,
        "citation_ref": citation_ref,
    }
    citation = Citation(
        source_type=SourceType.CONFLUENCE,
        label=f"Space {item['name']} ({key})"[:512],
        uri=url,
        locator={"space_key": key},
        retrieved_at=_now(),
    )
    return item, citation


def map_child(
    raw: dict[str, Any], *, base_url: str, citation_ref: int, links_base: str | None = None
) -> tuple[dict[str, Any], Citation]:
    base = links_base or base_url
    page_id = str(raw["id"])
    version_info = raw.get("version") or {}
    url = _page_url(raw, base)
    item = {
        "id": page_id,
        "title": raw.get("title", ""),
        "url": url,
        "updated_at": version_info.get("when"),
        "citation_ref": citation_ref,
    }
    citation = Citation(
        source_type=SourceType.CONFLUENCE,
        label=str(item["title"])[:512] or page_id,
        uri=url,
        locator={"page_id": page_id, "version": version_info.get("number")},
        retrieved_at=_now(),
    )
    return item, citation
