"""`build_server()` for mcp-confluence (ADR-0002): a plain `FastMCP` with the 4 tools and
the `dev_knowledge_lookup` prompt registered; transport and startup gating are
`mcp_common.runtime.serve`'s job."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings, SourceMisconfiguredError, load_settings
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.runtime import build_server as build_base_server

from mcp_confluence.client import SOURCE, ConfluenceClient
from mcp_confluence.prompts import SERVER_INSTRUCTIONS, register_prompts
from mcp_confluence.read_api import ConfluenceReadApi
from mcp_confluence.settings import Settings
from mcp_confluence.tools import register_tools

__all__ = ["SERVER_NAME", "build_server", "build_read_api"]

SERVER_NAME = "mcp-confluence"


def build_read_api(
    settings: Settings | None = None, common: CommonSettings | None = None
) -> ConfluenceReadApi:
    common = common or CommonSettings()
    settings = settings or load_settings(Settings, source=SOURCE)
    return ConfluenceReadApi(ConfluenceClient(settings, common=common), settings, common)


def build_server(
    read_api: ConfluenceReadApi | None = None,
    *,
    settings: Settings | None = None,
    common: CommonSettings | None = None,
) -> FastMCP:
    """Register tools lazily: the read API (and therefore the credentials) is only built on
    the first tool call, so `tools/list` and `tools-dump` never need a configured source."""
    holder: list[ConfluenceReadApi] = [read_api] if read_api else []

    def api() -> ConfluenceReadApi:
        if not holder:
            try:
                holder.append(build_read_api(settings, common))
            except SourceMisconfiguredError as exc:
                raise ToolError(
                    ErrorCode.SOURCE_MISCONFIGURED,
                    str(exc),
                    SOURCE,
                    False,
                    details={"missing_env": exc.missing_vars},
                ) from exc
        return holder[0]

    mcp = build_base_server(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_tools(mcp, api, common)
    register_prompts(mcp)
    return mcp
