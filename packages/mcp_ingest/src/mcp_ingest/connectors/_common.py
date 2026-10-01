"""Helpers shared by the connectors."""

from __future__ import annotations

from datetime import UTC, datetime

from mcp_common.config import SourceMisconfiguredError, SourceSettingsBase, load_settings

from mcp_ingest.connectors.base import ConnectorStatus

__all__ = ["parse_ts", "source_settings_status"]


def parse_ts(value: object) -> datetime | None:
    """ISO-8601 (with `Z` or an offset) -> aware UTC datetime; anything else -> None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def source_settings_status(
    settings_cls: type[SourceSettingsBase], source: str, *, extra_missing: list[str]
) -> tuple[ConnectorStatus, SourceSettingsBase | None]:
    """Is the *source package's* own `MCP_<SOURCE>_*` configuration present?

    Returns the status (with the exact missing env var names) and the loaded settings.
    """
    missing = list(extra_missing)
    loaded = None
    try:
        loaded = load_settings(settings_cls, source=source)
    except SourceMisconfiguredError as exc:
        missing = [*exc.missing_vars, *missing]
    return ConnectorStatus(enabled=True, configured=not missing, missing_env=missing), loaded
