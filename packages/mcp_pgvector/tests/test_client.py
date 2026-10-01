"""T-062: settings, version parsing and error mapping of the Postgres client (no database)."""

from __future__ import annotations

import pytest
from mcp_common.config import SourceMisconfiguredError, load_settings
from mcp_common.errors import ErrorCode
from mcp_pgvector.client import (
    ALLOWED_STATEMENTS,
    CONNECT_TIMEOUT_S,
    STATEMENT_TIMEOUT_MS,
    Capabilities,
    _dsn_host,
    parse_version,
    to_tool_error,
)
from mcp_pgvector.settings import Settings
from psycopg import OperationalError, ProgrammingError
from psycopg import errors as pgerrors


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.8.0", (0, 8, 0)), ("0.6.0", (0, 6, 0)), ("0.8", (0, 8)), ("0.7.4-beta", (0, 7, 4)),
     ("", None), (None, None), ("dev", None)],
)  # fmt: skip
def test_parse_version(text: str | None, expected: tuple[int, ...] | None) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("version", "iterative"),
    [((0, 8, 0), True), ((0, 8, 1), True), ((0, 9, 0), True), ((1, 0, 0), True),
     ((0, 7, 4), False), ((0, 6, 0), False), (None, False)],
)  # fmt: skip
def test_iterative_scan_needs_pgvector_08(version: tuple[int, ...] | None, iterative: bool) -> None:
    assert Capabilities(version).supports_iterative_scan is iterative


def test_documented_timeouts_fit_the_25s_tool_deadline() -> None:
    assert CONNECT_TIMEOUT_S == 3 and STATEMENT_TIMEOUT_MS == 15_000
    assert CONNECT_TIMEOUT_S + STATEMENT_TIMEOUT_MS / 1000 < 25


def test_the_statement_allowlist_has_no_catch_all_entry() -> None:
    assert "search" in ALLOWED_STATEMENTS and "*" not in ALLOWED_STATEMENTS


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (pgerrors.QueryCanceled("canceling statement due to statement timeout"),
         ErrorCode.UPSTREAM_TIMEOUT, True),
        (pgerrors.InsufficientPrivilege("permission denied"), ErrorCode.FORBIDDEN, False),
        (pgerrors.UndefinedTable('relation "kb.chunks" does not exist'),
         ErrorCode.SOURCE_MISCONFIGURED, False),
        (pgerrors.InvalidSchemaName('schema "kb" does not exist'),
         ErrorCode.SOURCE_MISCONFIGURED, False),
        (pgerrors.InvalidPassword("password authentication failed for user x"),
         ErrorCode.UNAUTHORIZED, False),
        (pgerrors.InvalidAuthorizationSpecification("role x does not exist"),
         ErrorCode.UNAUTHORIZED, False),
        (OperationalError("connection timeout expired"), ErrorCode.UPSTREAM_TIMEOUT, True),
        (OperationalError("connection is bad: Connection refused"),
         ErrorCode.UPSTREAM_UNAVAILABLE, True),
        (RuntimeError("boom"), ErrorCode.INTERNAL, False),
    ],
)  # fmt: skip
def test_psycopg_errors_map_to_contract_error_codes(exc, code, retryable) -> None:
    error = to_tool_error(exc, "db.internal")
    assert error.code == code and error.retryable is retryable and error.source == "pgvector"
    network = isinstance(exc, OperationalError) and not isinstance(exc, pgerrors.QueryCanceled)
    if network and code in {ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE}:
        assert "VPN" in error.details["hint"] and "db.internal" in error.details["hint"]
    if isinstance(exc, pgerrors.QueryCanceled):
        assert "statement_timeout" in error.message  # not a network problem: no VPN hint


def test_error_messages_never_echo_connection_strings_or_passwords() -> None:
    exc = OperationalError("connection to server at 10.0.0.1 failed: password=hunter2")
    error = to_tool_error(exc, "10.0.0.1")
    assert "hunter2" not in error.message and "hunter2" not in str(error.details)


def test_dsn_host_is_extracted_without_the_password() -> None:
    assert _dsn_host("postgresql://u:secret@db.internal:5432/kb") == "db.internal"
    assert _dsn_host("host=pg.local dbname=kb user=u password=secret") == "pg.local"
    assert _dsn_host("postgresql://u@/kb") is None


def test_dsn_host_of_a_malformed_dsn_is_none() -> None:
    assert _dsn_host("%%% not a dsn %%%") is None or isinstance(_dsn_host("%%% not a dsn %%%"), str)


def test_programming_errors_that_are_not_ours_map_to_internal() -> None:
    assert to_tool_error(ProgrammingError("syntax"), None).code == ErrorCode.INTERNAL


# -- settings ------------------------------------------------------------------------------------


def test_dsn_is_required_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PGVECTOR_DSN", raising=False)
    with pytest.raises(SourceMisconfiguredError) as exc:
        load_settings(Settings, source="pgvector")
    assert exc.value.missing_vars == ["MCP_PGVECTOR_DSN"]


def test_blank_dsn_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PGVECTOR_DSN", "   ")
    with pytest.raises(SourceMisconfiguredError):
        load_settings(Settings, source="pgvector")


def test_dsn_secret_never_in_repr_and_file_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MCP_PGVECTOR_DSN", "postgresql://mcp_query_ro:topsecret@h/kb")
    assert "topsecret" not in repr(load_settings(Settings))
    monkeypatch.delenv("MCP_PGVECTOR_DSN")
    secret = tmp_path / "dsn"
    secret.write_text("postgresql://mcp_query_ro:fromfile@h/kb\n")
    monkeypatch.setenv("MCP_PGVECTOR_DSN_FILE", str(secret))
    assert load_settings(Settings).dsn.get_secret_value().endswith("fromfile@h/kb")


def test_embedding_settings_are_shared_with_the_ingest_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_PGVECTOR_DSN", "postgresql://mcp_query_ro@h/kb")
    monkeypatch.setenv("MCP_INGEST_EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    monkeypatch.delenv("MCP_PGVECTOR_EMBEDDING_MODEL", raising=False)
    settings = load_settings(Settings)
    assert settings.embedding_settings().model == "intfloat/multilingual-e5-large"
    monkeypatch.setenv("MCP_PGVECTOR_EMBEDDING_MODEL", "BAAI/bge-m3")
    assert load_settings(Settings).embedding_settings().model == "BAAI/bge-m3"
