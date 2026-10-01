"""T-006: mcp_common.logging — JSON-to-stderr formatting + the stdout guard (R2)."""

from __future__ import annotations

import json
import logging
import sys

import pytest
from mcp_common.logging import (
    STANDARD_LOG_FIELDS,
    StdoutGuardViolation,
    get_logger,
    install_stdout_guard,
    setup_logging,
    uninstall_stdout_guard,
)


@pytest.fixture(autouse=True)
def _restore_stdout():
    real_stdout = sys.stdout
    yield
    uninstall_stdout_guard()
    sys.stdout = real_stdout


def test_print_while_guard_installed_raises() -> None:
    install_stdout_guard()

    with pytest.raises(StdoutGuardViolation):
        print("this must never reach stdout")  # noqa: T201 - this is the thing under test


def test_guard_is_idempotent_to_install_twice() -> None:
    install_stdout_guard()
    install_stdout_guard()

    with pytest.raises(StdoutGuardViolation):
        sys.stdout.write("x")


def test_uninstall_restores_real_stdout() -> None:
    real = sys.stdout
    install_stdout_guard()
    uninstall_stdout_guard()

    assert sys.stdout is real


class _FakeRealStdout:
    """Stand-in for the real `sys.stdout`: has a `.buffer` the real stdio transport
    writes to directly, bypassing `.write()` entirely."""

    def __init__(self) -> None:
        self.buffer = object()


def test_guard_forwards_buffer_attribute_for_real_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stdout = _FakeRealStdout()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    install_stdout_guard()

    assert sys.stdout.buffer is fake_stdout.buffer


def test_setup_logging_never_attaches_a_stdout_handler() -> None:
    logger_adapter = setup_logging("test-server")
    underlying_logger: logging.Logger = logger_adapter.logger

    for handler in underlying_logger.handlers:
        stream = getattr(handler, "stream", None)
        assert stream is not sys.__stdout__


def test_log_record_is_json_with_standard_fields_and_goes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = setup_logging("test-server", level="INFO")

    logger.info(
        "tool call finished",
        extra={
            "tool": "confluence_search_pages",
            "request_id": "req-1",
            "duration_ms": 42,
            "status": "ok",
            "items_returned": 3,
            "truncated": False,
            "redactions": 0,
        },
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    line = captured.err.strip()
    record = json.loads(line)

    assert record["server"] == "test-server"
    assert record["tool"] == "confluence_search_pages"
    assert record["status"] == "ok"
    assert record["duration_ms"] == 42
    for field in STANDARD_LOG_FIELDS:
        assert field in record


def test_full_query_text_is_never_emitted_at_info_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = setup_logging("test-server", level="INFO")

    logger.info(
        "search executed",
        extra={"query": "password=super-secret-value SELECT * FROM users"},
    )

    captured = capsys.readouterr()
    assert "super-secret-value" not in captured.err
    assert "password=" not in captured.err


def test_R_003_log_message_with_secret_shaped_string_is_scrubbed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`redact.py` documents `scrub()` as applying "to every log record" — the
    formatter is the one place every server's log handler goes through, so this is
    the structural guarantee, independent of whether any current call site happens
    to pass raw secret text as the log message."""
    logger = setup_logging("test-server", level="ERROR")

    logger.error(
        "upstream error: connection string postgresql://ingest:glpat-abcdefghijklmnopqrst@db"
    )

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert "glpat-abcdefghijklmnopqrst" not in record["message"]
    assert "«redacted:" in record["message"]


def test_get_logger_binds_server_without_setup(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("bound-server")
    logger = get_logger("bound-server")

    logger.warning("hello")

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip())
    assert record["server"] == "bound-server"
    assert record["level"] == "WARNING"
