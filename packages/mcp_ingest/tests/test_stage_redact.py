"""T-073: stages `normalize` -> `redact` (the policy gate), against a real database.

AC: FR-012/AC-001, FR-012/AC-002, R4 (no secret persisted), BR-003 (team-only, ADR-0016 A1/A2).
"""

from __future__ import annotations

from ingest_helpers import FakeConnector, doc, q, run, scalar
from mcp_ingest.pipeline.normalize import normalize_text
from mcp_ingest.pipeline.redact import (
    BLOCKED_BY_POLICY,
    Blocked,
    Prepared,
    apply_policy,
    deny_globs_from_env,
    path_denied,
)

AWS = "AKIAIOSFODNN7EXAMPLE"
TOKEN = "glpat-abcdefghijklmnopqrst"
GLOBS = ["*.env", "*secret*", "*.pem"]


def test_normalize_keeps_headings_as_markdown_and_drops_markup() -> None:
    html = (
        "<h2>Retry <b>policy</b></h2><p>Three&nbsp;tries.</p><ac:code><![CDATA[a < b]]></ac:code>"
    )
    text = normalize_text(html, "html")
    assert "## Retry policy" in text and "Three" in text and "a < b" in text
    assert "<" not in text.replace("a < b", "")


def test_normalize_plain_strips_control_characters_and_normalises_newlines() -> None:
    assert normalize_text("a\r\nb\x00c\n\n\n\nd", "plain") == "a\nbc\n\nd"


def test_FR_012_AC_001_redact_scrubs_secrets_before_anything_else() -> None:
    document = doc(content=f"<p>key {AWS} and token {TOKEN}</p>", title=f"x {AWS}")
    result = apply_policy(document, GLOBS)
    assert isinstance(result, Prepared)
    assert AWS not in result.text and TOKEN not in result.text and AWS not in (result.title or "")
    assert result.redactions >= 3 and len(result.content_hash) == 64


def test_content_hash_is_stable_and_follows_content() -> None:
    a = apply_policy(doc(content="<p>same</p>"), GLOBS)
    b = apply_policy(doc(content="<p>same</p>", title="renamed"), GLOBS)
    c = apply_policy(doc(content="<p>different</p>"), GLOBS)
    assert isinstance(a, Prepared) and isinstance(b, Prepared) and isinstance(c, Prepared)
    assert a.content_hash == b.content_hash != c.content_hash


def test_BR_003_a_restricted_document_is_blocked_by_policy() -> None:
    result = apply_policy(doc(visibility="restricted"), GLOBS)
    assert isinstance(result, Blocked) and result.code == BLOCKED_BY_POLICY
    assert "team-only" in result.reason


def test_deny_glob_on_the_path_blocks_even_when_the_connector_did_not() -> None:
    for path in (".env", "config/prod.env", "keys/server.PEM", "docs/my-secret-notes.md"):
        assert isinstance(apply_policy(doc(path=path), GLOBS), Blocked), path
    assert isinstance(apply_policy(doc(path="docs/readme.md"), GLOBS), Prepared)
    assert path_denied(None, GLOBS) is False


def test_a_connector_side_block_is_honoured() -> None:
    assert isinstance(apply_policy(doc(blocked_reason="deny-glob"), GLOBS), Blocked)


def test_deny_globs_come_from_the_gitlab_env_with_the_adr_default(monkeypatch) -> None:
    monkeypatch.delenv("MCP_GITLAB_PATH_DENY", raising=False)
    assert "*.pem" in deny_globs_from_env()
    monkeypatch.setenv("MCP_GITLAB_PATH_DENY", "*.key, *.TOKEN")
    assert deny_globs_from_env() == ["*.key", "*.token"]


# -- integration: a real database ------------------------------------------------------------


def test_R4_a_secret_in_the_source_never_reaches_kb_chunks(rw_dsn: str) -> None:
    content = (
        f"<h1>Ops</h1><p>The deploy key is {AWS} and the gitlab token {TOKEN}. Rotate often.</p>"
    )
    connector = FakeConnector("confluence", [doc("1", content=content, title=f"Ops {AWS}")])
    report = run(rw_dsn, {"confluence": connector})
    assert report.status == "success"
    joined = " ".join(r[0] for r in q(rw_dsn, "SELECT content FROM kb.chunks"))
    assert "Rotate often" in joined  # the rest of the page is kept
    for secret in (AWS, TOKEN):
        assert secret not in joined
        assert (
            scalar(rw_dsn, "SELECT count(*) FROM kb.chunks WHERE content LIKE %s", (f"%{secret}%",))
            == 0
        )
        assert (
            scalar(
                rw_dsn, "SELECT count(*) FROM kb.documents WHERE title LIKE %s", (f"%{secret}%",)
            )
            == 0
        )
    assert scalar(rw_dsn, "SELECT (metadata->>'redactions')::int FROM kb.documents") >= 3


def test_FR_012_AC_002_blocked_documents_are_visible_in_ingest_failures(rw_dsn: str) -> None:
    connector = FakeConnector(
        "gitlab",
        [
            doc("42:blob:.env", source_type="gitlab", content="", path=".env",
                blocked_reason="deny-glob", container="pay/api"),
            doc("42:blob:keys/server.pem", source_type="gitlab", path="keys/server.pem",
                content="-----BEGIN PRIVATE KEY-----", container="pay/api"),
            doc("42:blob:README.md", source_type="gitlab", content="# Readme\n\nhello world",
                fmt="markdown", path="README.md", container="pay/api"),
        ],
    )  # fmt: skip
    report = run(rw_dsn, {"gitlab": connector})
    assert report.status == "success", "a policy decision is not a failure of the run"
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents") == 1
    rows = q(rw_dsn, "SELECT source_id, stage, code FROM kb.ingest_failures ORDER BY source_id")
    assert rows == [
        ("42:blob:.env", "redact", "blocked_by_policy"),
        ("42:blob:keys/server.pem", "redact", "blocked_by_policy"),
    ]
    assert "BEGIN PRIVATE KEY" not in " ".join(
        r[0] for r in q(rw_dsn, "SELECT content FROM kb.chunks")
    )


def test_ADR_0016_a_restricted_document_is_rejected_and_the_corpus_stays_team_only(
    rw_dsn: str,
) -> None:
    connector = FakeConnector(
        "confluence",
        [doc("1"), doc("2", visibility="restricted", title="HR salaries"), doc("3")],
    )
    run(rw_dsn, {"confluence": connector})
    assert q(rw_dsn, "SELECT source_id FROM kb.documents ORDER BY 1") == [("1",), ("3",)]
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.documents WHERE visibility <> 'team' AND deleted_at IS NULL",
        )
        == 0
    )
    (failure,) = q(rw_dsn, "SELECT source_id, stage, code FROM kb.ingest_failures")
    assert failure == ("2", "redact", "blocked_by_policy")


def test_ADR_0016_A2_team_to_restricted_relabel_purges_chunks_and_tombstones(rw_dsn: str) -> None:
    page = doc("7", content="<h1>Runbook</h1><p>Failover procedure for the payments database.</p>")
    run(rw_dsn, {"confluence": FakeConnector("confluence", [page])})
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") >= 1

    # The page gets a read restriction at the source; its content is unchanged (hash-skip case).
    relabelled = doc(
        "7", content="<h1>Runbook</h1><p>Failover procedure for the payments database.</p>",
        visibility="restricted",
    )  # fmt: skip
    run(rw_dsn, {"confluence": FakeConnector("confluence", [relabelled])})

    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") == 0
    (row,) = q(rw_dsn, "SELECT deleted_at IS NOT NULL, visibility FROM kb.documents")
    assert row == (True, "restricted")
    assert q(rw_dsn, "SELECT stage, code FROM kb.ingest_failures WHERE source_id = '7'") == [
        ("redact", "blocked_by_policy")
    ]
    assert (
        scalar(
            rw_dsn,
            "SELECT count(*) FROM kb.documents WHERE visibility <> 'team' AND deleted_at IS NULL",
        )
        == 0
    )


def test_a_document_that_becomes_team_again_is_ingested_and_its_failure_row_cleared(
    rw_dsn: str,
) -> None:
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("9", visibility="restricted")])})
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 1
    run(rw_dsn, {"confluence": FakeConnector("confluence", [doc("9")])})
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.ingest_failures") == 0
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.documents WHERE deleted_at IS NULL") == 1
    assert scalar(rw_dsn, "SELECT count(*) FROM kb.chunks") >= 1


def test_repeated_runs_do_not_inflate_attempts_of_a_policy_block(rw_dsn: str) -> None:
    blocked = FakeConnector("confluence", [doc("2", visibility="restricted")])
    for _ in range(3):
        run(rw_dsn, {"confluence": blocked})
    assert scalar(rw_dsn, "SELECT attempts FROM kb.ingest_failures") == 1
