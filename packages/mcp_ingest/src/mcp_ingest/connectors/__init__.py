"""Source connectors (T-069..T-072). They live behind `registry` so that importing
`mcp_ingest.connectors` stays cheap; see `base` for the contract between connector and pipeline."""

from mcp_ingest.connectors.base import (
    AsyncBridge,
    ConnectorStatus,
    Cursor,
    SourceConnector,
    SourceDocument,
)

__all__ = ["AsyncBridge", "ConnectorStatus", "Cursor", "SourceConnector", "SourceDocument"]
