#!/usr/bin/env python3
"""CHG-001 QA exploratory charter (EXP-1) — TC-090 adversarial, real pgvector + mcp-knowledge stdio.

QA-owned (lives under ``e2e/``). This does NOT touch product code. It is the exploratory probe the
QA regression charter asks for: the riskiest area in test-plan.md is the permission choke point #1
(a ``restricted`` document leaking into a candidate / context-pack / citation). The package tests
``test_permission_adversarial.py`` already prove this at unit/integration level with a fake grant
store; this probe re-proves it end to end over the REAL ``mcp-knowledge`` MCP server reading a REAL
pgvector database through the read-only ``mcp_query_ro`` role.

Scenario: seed one SOURCED document whose content matches the query but grant it ONLY to a
non-team principal (``restricted-group-x``), never to ``*team*`` (an ADR-0016 ``restricted``
document). The real stdio server builds the v1 ``CallerContext`` (team member, holds ``*team*`` and
nothing else), so the default-deny intersection finds no matching grant row for this document and
drops it before assembly. The document must appear **nowhere** -- not as a claim, not as a
citation; status must be ``insufficient_evidence`` / ``empty`` with a UNKNOWN first claim, never a
FACT. (This exercises the real server without any product-code change; flipping team membership is
not an env knob at v1.)

Isolation: a throw-away DB inside the running dev ``pgvector/pgvector`` container; the live
``mcp_kb`` DB is untouched; the DB and the throw-away read role are dropped at teardown. HF egress
is blocked, so embeddings come from the repo DeterministicFakeProvider over a local HTTP stub --
this proves the PERMISSION path, not semantic relevance.

Run:  uv run python e2e/qa_chg001_restricted_leak_probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_ingest.db import upgrade  # noqa: E402
from mcp_ingest.embedding.fake import DeterministicFakeProvider  # noqa: E402

MODEL = "fake/hashed-bow"
DIM = 1024
PROVIDER = DeterministicFakeProvider(dimensions=DIM, model_id=MODEL)

PG_CONTAINER = os.environ.get("MCP_TEST_PG_CONTAINER", "mcp-dev-postgres")
ADMIN_PW = os.environ.get("MCP_TEST_PG_ADMIN_PW", "mcp_admin_dev_password")
RO_PW = os.environ.get("MCP_TEST_PG_RO_PW", "mcp_query_ro_dev_password")

# A single RESTRICTED document: content matches the query, but it gets NO '*team*' read grant.
RESTRICTED_DOC = (
    "confluence", "secret1", "https://wiki.example.test/SEC/salary-bands",
    "Executive salary bands 2026", "SEC",
    "The payment worker retries failed transactions and the executive salary band for the "
    "payments lead is confidential at level L7 band 42.",
)
QUERY = "how many times does the payment worker retry failed transactions"


def _host_port() -> int:
    out = subprocess.run(
        ["docker", "port", PG_CONTAINER, "5432/tcp"], capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"container {PG_CONTAINER} does not publish 5432")
    return int(out.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _admin_dsn(port: int, database: str) -> str:
    return f"postgresql://mcp_admin:{ADMIN_PW}@127.0.0.1:{port}/{database}"


def _create_db(port: int) -> str:
    import psycopg

    name = f"t_exp1_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return name


def _drop_db(port: int, name: str) -> None:
    import psycopg

    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _make_ro_login(port: int, database: str, suffix: str) -> str:
    import psycopg
    from psycopg import sql

    role = f"exp1_ro_{suffix}"
    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE mcp_query_ro").format(
                sql.Identifier(role), sql.Literal(RO_PW)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
    return f"postgresql://{role}:{RO_PW}@127.0.0.1:{port}/{database}"


def _drop_role(port: int, role: str) -> None:
    import contextlib

    import psycopg
    from psycopg import sql

    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        with contextlib.suppress(psycopg.Error):
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _seed_restricted(port: int, database: str) -> None:
    """Seed one SOURCED document but DO NOT grant it (no document_permissions row) -> restricted."""
    import psycopg

    with psycopg.connect(_admin_dsn(port, database), autocommit=True) as conn:
        upgrade(conn)
        run_id = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status) VALUES ('confluence','success') "
            "RETURNING id"
        ).fetchone()[0]
        source_type, source_id, uri, title, container, text = RESTRICTED_DOC
        doc_id = conn.execute(
            "INSERT INTO kb.documents (source_type, source_id, source_uri, title, container, "
            "content_hash, last_seen_run_id) VALUES (%s,%s,%s,%s,%s,'h',%s) RETURNING id",
            (source_type, source_id, uri, title, container, run_id),
        ).fetchone()[0]
        vec = PROVIDER.embed_documents([text])[0]
        vec_lit = "[" + ",".join(str(x) for x in vec) + "]"
        conn.execute(
            "INSERT INTO kb.chunks (document_id, chunk_index, content, token_count, "
            "content_tsv, embedding, embedding_model) "
            "VALUES (%s,0,%s,20, to_tsvector('simple', %s), %s, %s)",
            (doc_id, text, text, vec_lit, PROVIDER.model_id),
        )
        # Grant this document ONLY to a non-team principal. The v1 caller holds `*team*` and
        # nothing else, so this grant does NOT match -> default-deny drops the document.
        conn.execute(
            "INSERT INTO kb.document_permissions (document_id, principal, grant_type) "
            "VALUES (%s, 'restricted-group-x', 'read')",
            (doc_id,),
        )
        # NOTE: intentionally NO '*team*' grant -> restricted for the default team caller.


def _start_embed_stub() -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if not urlparse(self.path).path.endswith("/embeddings"):
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            texts = json.loads(self.rfile.read(length))["input"]
            vecs = PROVIDER.embed_documents(texts)
            raw = json.dumps(
                {"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):  # silence
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _knowledge_env(ro_dsn: str, embed_url: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(("PATH", "HOME", "LANG", "VIRTUAL_ENV", "PYTHON"))
    }
    env.update(
        {
            "MCP_KNOWLEDGE_DSN": ro_dsn,
            "MCP_INGEST_EMBEDDING_PROVIDER": "http",
            "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
            "MCP_INGEST_EMBEDDING_MODEL": MODEL,
            "MCP_INGEST_EMBEDDING_DIMENSIONS": str(DIM),
            "MCP_KNOWLEDGE_RERANKER_ENABLED": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
        }
    )
    return env


async def _drive(ro_dsn: str, embed_url: str) -> dict[str, object]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_knowledge", "serve"],
        env=_knowledge_env(ro_dsn, embed_url),
        cwd=str(ROOT),
    )
    out: dict[str, object] = {}
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("search_company_knowledge", {"query": QUERY})
                out["result"] = {
                    "isError": bool(res.isError),
                    "structuredContent": res.structuredContent,
                }
    return out


def main() -> int:
    port = _host_port()
    embed_srv, embed_url = _start_embed_stub()
    db = _create_db(port)
    suf = db.rsplit("_", 1)[1]
    role = f"exp1_ro_{suf}"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    print(f"# CHG-001 EXP-1 restricted-leak probe (TC-090 adversarial) -- {stamp}Z")
    print(f"# pgvector throw-away DB in {PG_CONTAINER}:{port} (live mcp_kb untouched); db={db}")
    print("# one SOURCED doc matching the query, granted ONLY to 'restricted-group-x', "
          "not *team*\n")
    try:
        _seed_restricted(port, db)
        ro = _make_ro_login(port, db, suf)
        res = asyncio.run(_drive(ro, embed_url))
        payload = res["result"]["structuredContent"]  # type: ignore[index]
        blob = json.dumps(payload, ensure_ascii=False)

        claims = payload.get("claims") or []
        citations = payload.get("citations") or []
        status = payload.get("status")
        first_verdict = (claims or [{}])[0].get("grounding")

        # The restricted document must appear NOWHERE: not by uri, not by title, not by its secret.
        leaked_uri = RESTRICTED_DOC[2] in blob
        leaked_title = RESTRICTED_DOC[3] in blob
        leaked_secret = "L7 band 42" in blob
        any_fact = any((c.get("grounding") == "FACT") for c in claims)

        print("--- result (restricted corpus, un-granted caller) ---")
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:1600])

        no_leak = (
            not leaked_uri
            and not leaked_title
            and not leaked_secret
            and not any_fact
            and len(citations) == 0
            and status in ("insufficient_evidence", "empty")
            and first_verdict in ("UNKNOWN", None)
        )
        print("\n=== RESULT ===")
        print(f"status={status!r} first_verdict={first_verdict!r} "
              f"n_claims={len(claims)} n_citations={len(citations)}")
        print(f"leaked_uri={leaked_uri} leaked_title={leaked_title} "
              f"leaked_secret={leaked_secret} any_fact={any_fact}")
        print(f"TC-090 restricted doc never a candidate/pack/citation : "
              f"{'PASS' if no_leak else 'FAIL'}")
        return 0 if no_leak else 1
    finally:
        embed_srv.shutdown()
        _drop_db(port, db)
        _drop_role(port, role)
        print(f"\n# throw-away DB {db} + role {role} dropped")


if __name__ == "__main__":
    sys.exit(main())
