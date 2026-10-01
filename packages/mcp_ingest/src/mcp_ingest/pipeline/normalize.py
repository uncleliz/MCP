"""Stage `normalize` (T-073): source markup -> plain text with Markdown heading lines.

Generic HTML/XHTML goes through `mcp_common.content.normalize`; headings are first turned into
`#`-prefixed lines because that helper drops structure and the chunker needs it to keep an exact
`heading_path` for the citation.
"""

from __future__ import annotations

import html as html_lib
import re

from mcp_common.content import normalize as common_normalize

from mcp_ingest.connectors.base import SourceDocument

__all__ = ["normalize_document", "normalize_text"]

_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BLANKS = re.compile(r"\n{3,}")


def _heading_line(match: re.Match[str]) -> str:
    title = html_lib.unescape(_TAG.sub("", match.group(2))).strip()
    return f"\n<p>{'#' * int(match.group(1))} {title}</p>\n" if title else ""


def normalize_text(text: str, source_format: str) -> str:
    if source_format == "html":
        exposed = _CDATA.sub(lambda m: html_lib.escape(m.group(1)), text)  # macro bodies (code)
        result = common_normalize(_HEADING.sub(_heading_line, exposed), source_format="html")
    else:
        result = text
    result = _CONTROL.sub("", result.replace("\r\n", "\n").replace("\r", "\n"))
    return _BLANKS.sub("\n\n", result).strip()


def normalize_document(document: SourceDocument) -> str:
    return normalize_text(document.raw_content, document.source_format)
