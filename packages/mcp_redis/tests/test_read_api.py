"""T-050: bounds, decoding, redaction, deny-glob and paging of mcp_redis.read_api."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.testing import assert_envelope_invariants
from mcp_redis.read_api import RedisReadApi, decode_value


def _items(outcome) -> list[dict[str, Any]]:
    assert_envelope_invariants(outcome.result)
    return outcome.result.items


# -- redis_scan_keys ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_008_AC_001_scan_keys_pattern_type_ttl_and_citation(read_api: RedisReadApi):
    outcome = await read_api.scan_keys(pattern="session:*", limit=10)
    result = outcome.result
    assert result.status.value == "ok"
    assert {i["key"] for i in _items(outcome)} == {"session:abc123", "session:def456"}
    by_key = {i["key"]: i for i in result.items}
    assert by_key["session:abc123"]["type"] == "hash" and by_key["session:abc123"]["ttl_s"] == 1800
    assert by_key["session:def456"]["ttl_s"] is None  # persistent key
    citation = result.citations[by_key["session:abc123"]["citation_ref"]]
    assert citation.source_type.value == "redis" and citation.uri is None
    assert citation.locator == {"key": "session:abc123", "db": 0}


@pytest.mark.asyncio
async def test_scan_keys_type_filter_and_empty(read_api: RedisReadApi):
    lists = await read_api.scan_keys(type_filter="list")
    assert [i["key"] for i in lists.result.items] == ["queue:jobs"]
    none = await read_api.scan_keys(pattern="nomatch:*")
    assert none.result.status.value == "empty" and none.result.citations == []
    assert none.query_description and "nomatch:*" in none.query_description


@pytest.mark.asyncio
async def test_FR_008_AC_003_scan_uses_scan_never_the_key_listing_command(
    read_api: RedisReadApi, calls: list
):
    await read_api.scan_keys(pattern="*")
    names = {str(c[0]).upper() for c in calls}
    assert "SCAN" in names and names <= {"SCAN", "TYPE", "TTL"}


@pytest.mark.asyncio
async def test_scan_denied_keys_are_filtered_and_counted_in_warnings(read_api: RedisReadApi):
    outcome = await read_api.scan_keys(pattern="*", limit=100)
    keys = {i["key"] for i in outcome.result.items}
    assert "deploy.env" not in keys
    assert any("MCP_REDIS_KEY_DENY" in w and "1" in w for w in outcome.result.meta.warnings)


@pytest.mark.asyncio
async def test_scan_pagination_cursor_walks_the_whole_keyspace(client, common, fake_data, calls):
    from mcp_redis.client import RedisClient
    from redis_helpers import FakeRedis

    batched = RedisClient(
        client._settings,
        common=common,
        redis_factory=lambda db: FakeRedis(fake_data, db=db, calls=calls, scan_batch=3),
    )
    api = RedisReadApi(batched, common)
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        outcome = await api.scan_keys(pattern="*", limit=2, cursor=cursor)
        seen += [i["key"] for i in outcome.result.items]
        assert len(outcome.result.items) <= 2
        cursor = outcome.result.meta.next_cursor
        if not cursor:
            break
    expected = {k for k in fake_data[0] if k != "deploy.env"}
    assert set(seen) == expected and len(seen) == len(expected)  # no dup, no loss


@pytest.mark.asyncio
async def test_scan_cursor_bound_to_db_and_bad_cursor_rejected(read_api: RedisReadApi):
    first = await read_api.scan_keys(limit=1)
    cursor = first.result.meta.next_cursor
    assert cursor and first.result.meta.has_more
    with pytest.raises(ToolError) as exc:
        await read_api.scan_keys(limit=1, db=1, cursor=cursor)
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == "cursor"
    with pytest.raises(ToolError):
        await read_api.scan_keys(cursor="###")


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"limit": 0}, "limit"), ({"limit": 101}, "limit"), ({"db": 16}, "db"),
        ({"db": -1}, "db"), ({"pattern": "x" * 513}, "pattern"),
        ({"type_filter": "bogus"}, "type_filter"),
    ],
)  # fmt: skip
@pytest.mark.asyncio
async def test_scan_invalid_input(read_api: RedisReadApi, kwargs: dict, field: str):
    with pytest.raises(ToolError) as exc:
        await read_api.scan_keys(**kwargs)
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == field


# -- redis_get_key --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_008_AC_001_get_string_with_type_ttl_and_key_citation(read_api: RedisReadApi):
    outcome = await read_api.get_key(key="greeting")
    item = _items(outcome)[0]
    assert item["value"] == "xin chào" and item["type"] == "string" and item["db"] == 0
    assert item["encoding"] == "embstr" and item["ttl_s"] is None and item["truncated"] is False
    assert outcome.result.citations[0].locator == {"key": "greeting", "db": 0}
    assert outcome.result.meta.query_echo["key"] == "greeting"


@pytest.mark.asyncio
async def test_FR_008_AC_002_missing_key_is_explicit_not_found(read_api: RedisReadApi):
    outcome = await read_api.get_key(key="does-not-exist")
    assert outcome.result.status.value == "not_found" and outcome.result.items == []
    assert outcome.identifier and "does-not-exist" in outcome.identifier
    assert any("nil" in w for w in outcome.result.meta.warnings)


@pytest.mark.asyncio
async def test_R4_hash_value_is_scrubbed_and_redactions_counted(read_api: RedisReadApi):
    outcome = await read_api.get_key(key="session:abc123")
    item = _items(outcome)[0]
    assert item["value"]["user_id"] == "8891"
    assert "eyJ" not in json.dumps(item["value"]) and "redacted" in item["value"]["token"]
    assert item["redactions"] >= 1 and outcome.result.meta.redactions >= 1
    assert item["element_count"] == 3 and item["memory_usage_bytes"] == 312
    assert item["ttl_s"] == 1800 and item["encoding"] == "listpack"
    assert any("redacted" in w for w in outcome.result.meta.warnings)


@pytest.mark.asyncio
async def test_R4_json_string_sensitive_field_names_are_redacted(read_api: RedisReadApi):
    item = _items(await read_api.get_key(key="config:json"))[0]
    value = json.loads(item["value"])
    assert value["name"] == "svc" and value["n"] == 1
    assert "hunter2" not in item["value"] and item["redactions"] >= 1


@pytest.mark.asyncio
async def test_get_list_set_zset_stream_shapes(read_api: RedisReadApi):
    lst = _items(await read_api.get_key(key="queue:jobs", element_limit=2))[0]
    assert lst["value"] == ["j1", "j2"] and lst["element_count"] == 3 and lst["truncated"] is True
    st = _items(await read_api.get_key(key="tags"))[0]
    assert sorted(st["value"]) == ["a", "b"] and st["truncated"] is False
    z = _items(await read_api.get_key(key="leaders"))[0]
    assert z["value"] == [{"member": "bob", "score": 5.0}, {"member": "alice", "score": 10.0}]
    s = _items(await read_api.get_key(key="events"))[0]
    assert s["value"][0]["id"] == "1-0" and s["value"][1] == {"id": "2-0", "fields": {"k": "v"}}
    assert "abcdefghijklmnop" not in json.dumps(s["value"]) and s["redactions"] >= 1


@pytest.mark.asyncio
async def test_truncated_value_makes_status_partial(read_api: RedisReadApi, fake_data):
    fake_data[0]["big"] = {"type": "string", "value": "x" * 5000, "ttl": -1}
    outcome = await read_api.get_key(key="big", max_bytes=1024)
    item = outcome.result.items[0]
    assert outcome.result.status.value == "partial" and item["truncated"] is True
    assert outcome.result.meta.truncated and len(item["value"].encode()) <= 1024


@pytest.mark.asyncio
async def test_max_bytes_is_clamped_to_server_cap_with_warning(client, fake_data):
    from mcp_common.config import CommonSettings

    api = RedisReadApi(client, CommonSettings(max_output_bytes=2048))
    fake_data[0]["big"] = {"type": "string", "value": "x" * 5000, "ttl": -1}
    outcome = await api.get_key(key="big", max_bytes=65536)
    assert any("clamped" in w for w in outcome.result.meta.warnings)
    assert len(outcome.result.items[0]["value"].encode()) <= 2048


@pytest.mark.asyncio
async def test_non_utf8_string_is_returned_as_base64(read_api: RedisReadApi):
    outcome = await read_api.get_key(key="binary")
    item = _items(outcome)[0]
    assert item["value"].startswith("base64:")
    assert any("base64" in w for w in outcome.result.meta.warnings)


@pytest.mark.asyncio
async def test_denied_key_behaves_as_not_found_with_policy_warning(
    read_api: RedisReadApi, calls: list
):
    outcome = await read_api.get_key(key="deploy.env")
    assert outcome.result.status.value == "not_found"
    assert any("MCP_REDIS_KEY_DENY" in w for w in outcome.result.meta.warnings)
    assert calls == []  # the value was never even requested


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"key": ""}, "key"), ({"key": "k" * 1025}, "key"), ({"key": "k", "db": 16}, "db"),
        ({"key": "k", "element_limit": 0}, "element_limit"),
        ({"key": "k", "element_limit": 501}, "element_limit"),
        ({"key": "k", "max_bytes": 10}, "max_bytes"),
    ],
)  # fmt: skip
@pytest.mark.asyncio
async def test_get_key_invalid_input(read_api: RedisReadApi, kwargs: dict, field: str):
    with pytest.raises(ToolError) as exc:
        await read_api.get_key(**kwargs)
    assert exc.value.details["field"] == field


# -- redis_key_info -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_info_metadata_without_value(read_api: RedisReadApi):
    outcome = await read_api.key_info(key="session:abc123")
    item = _items(outcome)[0]
    assert item == {
        "key": "session:abc123", "exists": True, "type": "hash", "ttl_s": 1800, "db": 0,
        "encoding": "listpack", "memory_usage_bytes": 312, "length": 3, "citation_ref": 0,
    }  # fmt: skip
    assert "value" not in item


@pytest.mark.asyncio
async def test_key_info_lengths_per_type_and_missing(read_api: RedisReadApi):
    lengths = {}
    for key in ("greeting", "queue:jobs", "tags", "leaders", "events"):
        lengths[key] = _items(await read_api.key_info(key=key))[0]["length"]
    assert lengths == {"greeting": len("xin chào".encode()), "queue:jobs": 3, "tags": 2,
                       "leaders": 2, "events": 2}  # fmt: skip
    missing = await read_api.key_info(key="nope")
    assert missing.result.status.value == "not_found"
    denied = await read_api.key_info(key="deploy.env")
    assert denied.result.status.value == "not_found"
    assert any("MCP_REDIS_KEY_DENY" in w for w in denied.result.meta.warnings)


# -- redis_server_info --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_info_sections_dbsize_acl_user_and_readonly_confirmed(read_api: RedisReadApi):
    outcome = await read_api.server_info(sections=["memory", "keyspace"])
    item = _items(outcome)[0]
    assert item["sections"]["memory"]["used_memory_human"] == "1.00M"
    assert item["sections"]["keyspace"]["db0"].startswith("keys=")
    assert item["dbsize"] == 10 and item["acl_user"] == "mcp_ro"
    assert item["readonly_confirmed"] is True
    assert outcome.result.citations[0].locator == {"sections": ["memory", "keyspace"]}


@pytest.mark.asyncio
async def test_server_info_readonly_confirmed_false_and_null(client, common, fake_data, calls):
    from mcp_redis.client import RedisClient
    from redis_helpers import FakeRedis

    writer = RedisClient(
        client._settings, common=common,
        redis_factory=lambda db: FakeRedis(fake_data, acl_commands="-@all +get +set", db=db),
    )  # fmt: skip
    item = (await RedisReadApi(writer, common).server_info()).result.items[0]
    assert item["readonly_confirmed"] is False

    import redis.exceptions as rex

    class NoAcl(FakeRedis):
        async def execute_command(self, *args, **options):
            if str(args[0]).upper() == "ACL":
                raise rex.NoPermissionError("NOPERM")
            return await super().execute_command(*args, **options)

    blind = RedisClient(
        client._settings, common=common, redis_factory=lambda db: NoAcl(fake_data, db=db)
    )
    item = (await RedisReadApi(blind, common).server_info()).result.items[0]
    assert item["readonly_confirmed"] is None and item["acl_user"] is None


@pytest.mark.asyncio
async def test_server_info_rejects_unknown_section(read_api: RedisReadApi):
    with pytest.raises(ToolError) as exc:
        await read_api.server_info(sections=["bogus"])
    assert exc.value.details["field"] == "sections"


# -- decode_value ---------------------------------------------------------------------------------


def test_decode_value_formats() -> None:
    assert decode_value(b"hi", "auto") == ("hi", "utf8")
    assert decode_value(b'{"a": 1}', "json") == ('{"a": 1}', "json")
    assert decode_value(b"not json", "json")[1] == "utf8"  # falls back
    assert decode_value(b"\xff", "auto")[1] == "base64"
    assert decode_value(b"hi", "base64") == ("base64:aGk=", "base64")
    assert decode_value(b'{"a": 1}', "auto") == ('{"a": 1}', "json")
    assert decode_value(b"\xff", "utf8")[1] == "base64"
