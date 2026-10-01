"""`mcp-ingest sources` (T-069/T-079): the registered connectors and their configuration state."""

from __future__ import annotations

from mcp_ingest.connectors import registry
from mcp_ingest.reports import IngestSourceConfigRow, SourcesReport
from mcp_ingest.settings import Settings

__all__ = ["list_sources"]


def list_sources(settings: Settings) -> SourcesReport:
    rows = [
        IngestSourceConfigRow(
            source_type=spec.source_type,
            connector=spec.class_name,
            enabled=status.enabled,
            configured=status.configured,
            missing_env=status.missing_env,
        )
        for spec, status in registry.describe_all(settings)
    ]
    return SourcesReport(connectors=rows)
