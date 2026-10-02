"""T-121 (CHG-003 / FR-025 AC-002, ADR-0023 §6e#3) — the read-only Atlassian token
must never leak, scrubbed on BOTH the tool/result boundary AND the error/log path.

This is the adversarial regression for the E-003 token-leak defect class, but for the
new opaque Atlassian Cloud API token: a forced error whose message/details interpolate
the configured credential must come out with the token value absent from
(a) the returned error envelope (result boundary) and (b) the stderr JSON log.

The guarantee is value-based (`register_secret`) so it holds even for an opaque token
that carries no recognisable `ATATT3`/shape/high-entropy signal — exactly the gap the
shape/label/entropy heuristics cannot close on their own. We assert the mechanism at
the two existing single choke points (`to_error_envelope`, `JSONStderrFormatter`) so
there is no new bypass path to maintain.
"""

from __future__ import annotations

import json
import logging

import pytest
from mcp_common.errors import ErrorCode, ToolError, to_error_envelope
from mcp_common.logging import JSONStderrFormatter
from mcp_common.redact import (
    clear_registered_secrets,
    register_dsn_secret,
    register_secret,
    scrub,
)

# An OPAQUE token: short (< 32 chars so the long-token/high-entropy pass never sees it),
# with lowercase runs and a dash, and no ATATT3 prefix — the kind the shape/label/entropy
# passes do NOT catch, so value-based redaction is the only thing that can scrub it.
_OPAQUE_ATLASSIAN_TOKEN = "confluence-ro-7h9k"  # noqa: S105 (fake, test fixture)
_REDACTED_MARKER = "«redacted:credential»"


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def test_opaque_token_is_not_caught_by_the_heuristics_without_registration() -> None:
    # Establishes WHY value-based redaction is needed: the opaque token slips the
    # shape/label/entropy passes when it is not registered.
    text, count = scrub(f"request failed talking to upstream with token {_OPAQUE_ATLASSIAN_TOKEN}")
    assert _OPAQUE_ATLASSIAN_TOKEN in text
    assert count == 0


def test_registered_token_is_scrubbed_by_value() -> None:
    register_secret(_OPAQUE_ATLASSIAN_TOKEN)
    text, count = scrub(f"Basic auth header used token {_OPAQUE_ATLASSIAN_TOKEN} to Confluence")
    assert _OPAQUE_ATLASSIAN_TOKEN not in text
    assert count >= 1
    assert "«redacted:credential»" in text


def test_forced_error_envelope_does_not_leak_the_token() -> None:
    """(a) result boundary: an error carrying the token in message AND details is scrubbed."""
    register_secret(_OPAQUE_ATLASSIAN_TOKEN)
    # Simulate the credential path raising with the token interpolated into the error.
    error = ToolError(
        ErrorCode.UPSTREAM_ERROR,
        f"Confluence rejected the request (token={_OPAQUE_ATLASSIAN_TOKEN}).",
        "confluence",
        True,
        details={"request": {"authorization": f"Basic {_OPAQUE_ATLASSIAN_TOKEN}"}},
    )
    envelope = to_error_envelope(error)
    serialised = json.dumps(envelope)
    assert _OPAQUE_ATLASSIAN_TOKEN not in serialised
    # The scrub ran on both message and details (marker visible in the raw envelope values).
    assert _REDACTED_MARKER in envelope["error"]["message"]
    assert _REDACTED_MARKER in json.dumps(envelope["error"]["details"], ensure_ascii=False)


def test_forced_error_stderr_log_does_not_leak_the_token() -> None:
    """(b) error/log path: the stderr JSON formatter scrubs the token in message + exc_info."""
    register_secret(_OPAQUE_ATLASSIAN_TOKEN)
    formatter = JSONStderrFormatter()

    # Token in the log message itself.
    record = logging.LogRecord(
        name="mcp.confluence",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="auth failed using token %s",
        args=(_OPAQUE_ATLASSIAN_TOKEN,),
        exc_info=None,
    )
    rendered = formatter.format(record)
    assert _OPAQUE_ATLASSIAN_TOKEN not in rendered
    assert _REDACTED_MARKER in json.loads(rendered)["message"]

    # Token inside an exception traceback carried on the record.
    try:
        raise RuntimeError(f"upstream error with token {_OPAQUE_ATLASSIAN_TOKEN}")
    except RuntimeError:
        import sys

        exc_record = logging.LogRecord(
            name="mcp.confluence",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="credential path blew up",
            args=(),
            exc_info=sys.exc_info(),
        )
    rendered_exc = formatter.format(exc_record)
    assert _OPAQUE_ATLASSIAN_TOKEN not in rendered_exc


def test_scrub_applies_on_both_boundaries_in_one_assertion() -> None:
    """TC-115 core: the SAME token, forced through BOTH boundaries, is absent from both."""
    register_secret(_OPAQUE_ATLASSIAN_TOKEN)

    # Result boundary.
    envelope = to_error_envelope(
        ToolError(
            ErrorCode.UNAUTHORIZED,
            f"401 from Confluence (token {_OPAQUE_ATLASSIAN_TOKEN})",
            "confluence",
            False,
        )
    )
    # Log boundary.
    log_line = JSONStderrFormatter().format(
        logging.LogRecord(
            name="mcp.confluence",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=f"retrying with token {_OPAQUE_ATLASSIAN_TOKEN}",
            args=(),
            exc_info=None,
        )
    )
    assert _OPAQUE_ATLASSIAN_TOKEN not in json.dumps(envelope)
    assert _OPAQUE_ATLASSIAN_TOKEN not in log_line


def test_short_or_blank_registration_is_ignored_and_does_not_scrub_ordinary_text() -> None:
    # Guard: a blank/placeholder token must not turn scrub() into a redact-everything no-op.
    register_secret("")
    register_secret("   ")
    register_secret("short")  # below the minimum length
    text, count = scrub("the service short name is handled normally here")
    assert count == 0
    assert text == "the service short name is handled normally here"


def test_extra_secrets_argument_scrubs_without_registration() -> None:
    # A caller can scrub a one-off secret for a single call without the global registry.
    text, count = scrub(
        f"token {_OPAQUE_ATLASSIAN_TOKEN} here", extra_secrets=(_OPAQUE_ATLASSIAN_TOKEN,)
    )
    assert _OPAQUE_ATLASSIAN_TOKEN not in text
    assert count >= 1
    # Nothing registered globally, so a fresh scrub without the arg leaves it.
    plain, plain_count = scrub(f"token {_OPAQUE_ATLASSIAN_TOKEN} here")
    assert _OPAQUE_ATLASSIAN_TOKEN in plain
    assert plain_count == 0


def test_register_dsn_secret_registers_password_and_full_dsn() -> None:
    # E-mcp-data-platform-009: register_dsn_secret registers both the password component and the
    # whole DSN, so neither can leak verbatim.
    dsn = "postgresql://mcp_query_ro:db-ro-7h9k2p@db.acme.test:5432/mcp_kb"
    register_dsn_secret(dsn)
    text, count = scrub(f"psycopg OperationalError connecting with {dsn}")
    assert "db-ro-7h9k2p" not in text
    assert dsn not in text
    assert count >= 1
    # The bare password alone is also scrubbed.
    pw_text, pw_count = scrub("the password is db-ro-7h9k2p today")
    assert "db-ro-7h9k2p" not in pw_text
    assert pw_count >= 1


def test_register_dsn_secret_ignores_empty_and_passwordless_dsn() -> None:
    register_dsn_secret(None)
    register_dsn_secret("")
    # A DSN with no password: the whole string is registered, but there is no password branch.
    register_dsn_secret("postgresql://mcp_query_ro@db.acme.test:5432/mcp_kb")
    text, count = scrub("the role mcp_query_ro connects to db.acme.test normally")
    assert count == 0
    assert text == "the role mcp_query_ro connects to db.acme.test normally"
