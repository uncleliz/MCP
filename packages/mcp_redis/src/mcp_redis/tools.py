"""The four Redis tools (FR-008). Names, input schemas and outputs are dictated by
`api-contract.yaml`; `tools.snapshot.json` + `tests/test_contract.py` keep them in sync.

Each function forwards to `RedisReadApi` and returns a `ToolOutcome`; deadline, error
envelope and text rendering are applied by `register_tool`.
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp_common.config import CommonSettings
from mcp_common.readonly import readonly_tool
from mcp_common.tooling import CursorParam, LimitParam, MaxBytesParam, ToolOutcome, register_tool
from pydantic import Field

from mcp_redis.client import SOURCE
from mcp_redis.read_api import RedisReadApi

__all__ = ["TOOL_DESCRIPTIONS", "make_tools", "register_tools"]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "redis_scan_keys": (
        "Quét key theo pattern bằng SCAN có cursor, không chặn server. "
        "Key khớp deny-glob bị loại và được đếm trong cảnh báo."
    ),
    "redis_get_key": (
        "Đọc giá trị một key theo đúng kiểu dữ liệu, đã redaction và cắt theo "
        "element_limit/max_bytes. Key không tồn tại trả not_found."
    ),
    "redis_key_info": (
        "Metadata của key (kiểu, TTL, encoding, bộ nhớ, độ dài) mà không trả giá trị. "
        "Dùng trước redis_get_key khi key có thể rất lớn."
    ),
    "redis_server_info": (
        "Thông tin server theo section (memory, clients, keyspace) cùng DBSIZE và ACL user "
        "đang dùng. readonly_confirmed cho biết credential có chỉ đọc hay không."
    ),
}

Db = Annotated[int, Field(ge=0, le=15)]
KeyName = Annotated[str, Field(min_length=1, max_length=1024)]
InfoSection = Literal[
    "server", "clients", "memory", "persistence", "stats", "replication", "keyspace"
]

ApiFactory = Callable[[], RedisReadApi]


def make_tools(api: ApiFactory) -> dict[str, Callable[..., Any]]:
    @readonly_tool
    async def redis_scan_keys(
        pattern: Annotated[str, Field(max_length=512)] = "*",
        type_filter: Literal["string", "list", "set", "zset", "hash", "stream"] | None = None,
        db: Db = 0,
        limit: LimitParam = 20,
        cursor: CursorParam = None,
    ) -> ToolOutcome:
        return await api().scan_keys(
            pattern=pattern, type_filter=type_filter, db=db, limit=limit, cursor=cursor
        )

    @readonly_tool
    async def redis_get_key(
        key: KeyName,
        db: Db = 0,
        element_limit: Annotated[int, Field(ge=1, le=500)] = 100,
        max_bytes: MaxBytesParam = 65536,
    ) -> ToolOutcome:
        return await api().get_key(key=key, db=db, element_limit=element_limit, max_bytes=max_bytes)

    @readonly_tool
    async def redis_key_info(key: KeyName, db: Db = 0) -> ToolOutcome:
        return await api().key_info(key=key, db=db)

    @readonly_tool
    async def redis_server_info(
        sections: Annotated[list[InfoSection], Field(max_length=7)] = [  # noqa: B006
            "server",
            "memory",
            "clients",
            "keyspace",
        ],
    ) -> ToolOutcome:
        return await api().server_info(sections=list(sections))

    return {
        fn.__name__: fn
        for fn in (redis_scan_keys, redis_get_key, redis_key_info, redis_server_info)
    }


def register_tools(mcp: FastMCP, api: ApiFactory, common: CommonSettings | None = None) -> None:
    for name, fn in make_tools(api).items():
        register_tool(
            mcp,
            fn,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            source=SOURCE,
            common_settings=common,
        )
