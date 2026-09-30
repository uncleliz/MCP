"""T-009: `mcp_common.render` — the text rendering every tool returns alongside
`structuredContent` (contract `info.x-text-rendering`; this text is what Claude
actually reads, so it is part of the contract, not an afterthought).

Implements the 6 sections `info.x-text-rendering.sections` documents:

1. Status line — `ok`/`partial` → "<n> kết quả từ <source>"; `empty` → the exact
   "Không tìm thấy <mô tả truy vấn> ở <source>." sentence; `not_found` →
   "<định danh> không tồn tại ở <source>.".
2. Item bodies, each opening with `[n]` matching its `citation_ref`.
3. A pass-through of each item's already-`wrap_untrusted()`-wrapped free-text field
   (rendering does not wrap a second time — ADR-0015 A2).
4. The final "Nguồn:" section built from `citations[]`; absent when
   `status ∈ {empty, not_found}`.
5. A "Lưu ý:" line when `meta.truncated`, `meta.redactions > 0`, or `meta.warnings`
   is non-empty.
6. A freshness line when `meta.data_freshness` is set (mcp-pgvector only, NFR-004).
"""

from __future__ import annotations

from typing import Any

from mcp_common.envelope import Citation, ResultStatus, SourceType, ToolResult

__all__ = ["render_text", "SOURCE_LABELS"]

SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.CONFLUENCE: "Confluence",
    SourceType.GITLAB: "GitLab",
    SourceType.OPENSEARCH: "OpenSearch",
    SourceType.KIBANA: "Kibana",
    SourceType.CLOUDWATCH: "CloudWatch",
    SourceType.KAFKA: "Kafka",
    SourceType.REDIS: "Redis",
    SourceType.SQS: "SQS",
    SourceType.SNS: "SNS",
    SourceType.PGVECTOR: "cơ sở dữ liệu ngữ nghĩa (pgvector)",
}

# Item dict keys treated as a heading when rendering `[n] <heading>` (first match wins).
_TITLE_KEYS = ("title", "label", "name", "key")
# Item dict keys skipped in the generic "key: value" fallback body (already rendered
# elsewhere, or internal bookkeeping Claude does not need to see).
_SKIP_KEYS = {"citation_ref", *_TITLE_KEYS}


def _describe_query_echo(query_echo: dict[str, Any]) -> str:
    if not query_echo:
        return "kết quả phù hợp"
    return ", ".join(f"{key}={value}" for key, value in query_echo.items())


def _format_locator(citation: Citation) -> str:
    if citation.uri:
        return citation.uri
    if citation.locator:
        return ", ".join(f"{key}={value}" for key, value in citation.locator.items())
    return "(không có định danh)"


def _render_item(index: int, item: dict[str, Any]) -> str:
    title = next((item[key] for key in _TITLE_KEYS if item.get(key)), None)
    lines = [f"[{index}] {title}" if title else f"[{index}]"]
    for key, value in item.items():
        if key in _SKIP_KEYS:
            continue
        if isinstance(value, str) and "<untrusted-content" in value:
            # Already wrapped by the tool layer via mcp_common.content.wrap_untrusted()
            # — render it verbatim, do not wrap a second time (ADR-0015 A2).
            lines.append(value)
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def render_text(
    result: ToolResult,
    *,
    query_description: str | None = None,
    identifier: str | None = None,
) -> str:
    """Render the text Claude reads alongside `structuredContent` for one `ToolResult`.

    `query_description` / `identifier` let the caller (a specific tool) supply the
    human-readable phrase for the `empty`/`not_found` sentence; both fall back to a
    description derived from `meta.query_echo` if not given.
    """
    source_label = SOURCE_LABELS[result.meta.source]
    lines: list[str] = []

    if result.status in (ResultStatus.OK, ResultStatus.PARTIAL):
        lines.append(f"{len(result.items)} kết quả từ {source_label}.")
    elif result.status == ResultStatus.EMPTY:
        description = query_description or _describe_query_echo(result.meta.query_echo)
        lines.append(f"Không tìm thấy {description} ở {source_label}.")
    elif result.status == ResultStatus.NOT_FOUND:
        ident = identifier or _describe_query_echo(result.meta.query_echo) or "Mục yêu cầu"
        lines.append(f"{ident} không tồn tại ở {source_label}.")

    for index, item in enumerate(result.items):
        lines.append("")
        lines.append(_render_item(index, item))

    if result.status in (ResultStatus.OK, ResultStatus.PARTIAL):
        lines.append("")
        lines.append("Nguồn:")
        for index, citation in enumerate(result.citations):
            lines.append(f"[{index}] {citation.label} — {_format_locator(citation)}")

    notes: list[str] = []
    if result.meta.truncated:
        notes.append("kết quả đã bị cắt (truncated)")
    if result.meta.redactions > 0:
        notes.append(f"{result.meta.redactions} chỗ đã bị che (redacted)")
    notes.extend(result.meta.warnings)
    if notes:
        lines.append("")
        lines.append("Lưu ý: " + "; ".join(notes))

    freshness = result.meta.data_freshness
    if freshness is not None:
        lines.append("")
        lines.append(
            f"Độ mới dữ liệu: cập nhật lần cuối {freshness.last_ingested_at}, "
            f"cũ {freshness.staleness_hours} giờ."
        )

    return "\n".join(lines)
