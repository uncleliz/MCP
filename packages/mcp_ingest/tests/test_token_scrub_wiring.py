"""E-mcp-data-platform-009 (FR-025 / NFR-014) — the PRODUCTION-WIRING complement to TC-115.

TC-115 (`mcp_common/tests/test_token_never_leaks.py`) proves the *mechanism*: once a secret
is `register_secret()`-ed, `scrub()` redacts it by value on both outbound choke points
(`to_error_envelope`, `JSONStderrFormatter`). It calls `register_secret()` itself.

This test proves the *wiring*: **constructing a source client with a configured opaque secret
registers that secret**, with no explicit `register_secret()` call here — so in production an
opaque Atlassian/GitLab/OpenSearch/Postgres credential (one the shape/label/entropy heuristics
cannot recognise) that reaches a log/result on an error path is scrubbed from BOTH:
  (a) the returned error envelope (result boundary), and
  (b) the stderr JSON log.

Covers Confluence + GitLab + OpenSearch + pgvector (DSN) — the Atlassian-only guarantee is now
uniform across sources. The registry is cleared around each test by the workspace-wide
`_isolate_registered_secrets` fixture (packages/conftest.py), so no secret leaks across tests.

No live creds, no network: each client is built with a fake opaque secret and nothing is called
on it beyond construction.
"""

from __future__ import annotations

import json
import logging

from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, ToolError, to_error_envelope
from mcp_common.logging import JSONStderrFormatter
from pydantic import SecretStr

# An OPAQUE secret: short (< 32 chars so the long-token/high-entropy pass never sees it), with
# lowercase runs and a dash, no recognisable prefix — exactly what value-based redaction exists
# for. If construction did NOT register it, these asserts would fail (proven negative in
# test_opaque_secret_is_not_scrubbed_before_any_client_is_built below).
_OPAQUE = "src-ro-7h9k2p"  # noqa: S105 - fake test fixture
_REDACTED_MARKER = "«redacted:credential»"


def _assert_scrubbed_on_both_boundaries(secret: str, source: str) -> None:
    """A forced error carrying `secret` in message AND details must be absent from the error
    envelope (result boundary) and from the stderr JSON log (log boundary)."""
    error = ToolError(
        ErrorCode.UPSTREAM_ERROR,
        f"{source} upstream rejected the request (token={secret}).",
        source,
        True,
        details={"request": {"authorization": f"Basic {secret}"}},
    )
    envelope = to_error_envelope(error)
    serialised = json.dumps(envelope, ensure_ascii=False)
    assert secret not in serialised, f"{source}: secret leaked into the result envelope"
    assert _REDACTED_MARKER in envelope["error"]["message"]

    log_line = JSONStderrFormatter().format(
        logging.LogRecord(
            name=f"mcp.{source}",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="auth failed using token %s",
            args=(secret,),
            exc_info=None,
        )
    )
    assert secret not in log_line, f"{source}: secret leaked into the stderr log"
    assert _REDACTED_MARKER in json.loads(log_line)["message"]


def test_opaque_secret_is_not_scrubbed_before_any_client_is_built() -> None:
    """Baseline: with the registry empty (fixture-cleared), the opaque secret slips scrub()."""
    from mcp_common.redact import scrub

    text, count = scrub(f"request failed with token {_OPAQUE}")
    assert _OPAQUE in text
    assert count == 0


def test_building_confluence_client_registers_the_token() -> None:
    """AC: constructing the Confluence client with a configured token wires the scrub guarantee."""
    from mcp_confluence.client import ConfluenceClient
    from mcp_confluence.settings import Settings as ConfluenceSettings

    ConfluenceClient(
        ConfluenceSettings(
            base_url="https://acme.atlassian.net/wiki",
            email="svc@acme.test",
            api_token=SecretStr(_OPAQUE),
        ),
        common=CommonSettings(http_backoff_base=0.0),
    )
    _assert_scrubbed_on_both_boundaries(_OPAQUE, "confluence")


def test_building_gitlab_client_registers_the_token() -> None:
    """AC: a second source — the GitLab PAT is registered at construction too."""
    from mcp_gitlab.client import GitLabClient
    from mcp_gitlab.settings import Settings as GitLabSettings

    GitLabClient(
        GitLabSettings(base_url="https://gitlab.acme.test", private_token=SecretStr(_OPAQUE)),
        common=CommonSettings(http_backoff_base=0.0),
    )
    _assert_scrubbed_on_both_boundaries(_OPAQUE, "gitlab")


def test_building_opensearch_client_registers_the_password() -> None:
    """AC: a third source — the OpenSearch password is registered at construction too."""
    from mcp_opensearch.client import OpenSearchClient
    from mcp_opensearch.settings import Settings as OpenSearchSettings

    OpenSearchClient(
        OpenSearchSettings(
            hosts="https://os.acme.test:9200",
            username="mcp_ro",
            password=SecretStr(_OPAQUE),
        ),
        # Inject a stub so no real opensearch-py client / network is created.
        os_client=object(),
    )
    _assert_scrubbed_on_both_boundaries(_OPAQUE, "opensearch")


def test_building_pgvector_client_registers_the_dsn_password() -> None:
    """AC: a DSN-backed source — the password inside the configured DSN is registered too."""
    from mcp_pgvector.client import PgVectorClient
    from mcp_pgvector.settings import Settings as PgVectorSettings

    dsn = f"postgresql://mcp_query_ro:{_OPAQUE}@db.acme.test:5432/mcp_kb"
    PgVectorClient(
        PgVectorSettings(dsn=SecretStr(dsn)),
        common=CommonSettings(),
    )
    # The bare password component scrubs...
    _assert_scrubbed_on_both_boundaries(_OPAQUE, "pgvector")
    # ...and so does the full DSN string if it is what leaks.
    _assert_scrubbed_on_both_boundaries(dsn, "pgvector")
