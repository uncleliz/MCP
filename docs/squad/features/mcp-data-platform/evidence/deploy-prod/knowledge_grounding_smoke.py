#!/usr/bin/env python3
"""CHG-001 T-110 sign-off smoke — grounding verdict over the REAL mcp-knowledge stdio server.

DEMO DATA ONLY — no real sources, no network beyond 127.0.0.1.
  * The pgvector store is a THROW-AWAY database created inside the running dev `pgvector/pgvector`
    container (`mcp-dev-postgres`, the exact engine the kb store went live on). The live `mcp_kb`
    database is never touched; the throw-away database is dropped at the end.
  * The full migration chain (incl. 0007 / 0007b / 0008) is applied with the real
    `mcp_ingest.db.upgrade`, so the content_tsv column + GIN index (0007b) and the source-authority
    tables (0008) that grounding reads are the real ones.
  * Hugging Face egress is blocked (NFR-003 / ADR-0010 / HF 403), so embeddings come from the
    repo's DeterministicFakeProvider exposed behind a local OpenAI-compatible HTTP endpoint — the
    exact seam the e2e suite uses. This proves the GROUNDING VERDICT path runs for real
    (no-source ⇒ UNKNOWN, sourced ⇒ FACT); it does NOT measure semantic relevance
    (CAB caveat / NFR-003 UNVERIFIED).

Everything else is real: the real `mcp-knowledge` MCP server (`python -m mcp_knowledge serve`)
answers real JSON-RPC `search_company_knowledge` tool calls over stdio, reading from the
throw-away pgvector DB through the read-only `mcp_query_ro` role, running permission choke point #1
(document_grants) then the deterministic grounding gate choke point #2 (verdict.py).

Run:  uv run python \
        docs/squad/features/mcp-data-platform/evidence/deploy-prod/knowledge_grounding_smoke.py
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
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

from mcp_ingest.db import upgrade  # noqa: E402
from mcp_ingest.embedding.fake import DeterministicFakeProvider  # noqa: E402

MODEL = "fake/hashed-bow"
DIM = 1024
PROVIDER = DeterministicFakeProvider(dimensions=DIM, model_id=MODEL)

PG_CONTAINER = os.environ.get("MCP_TEST_PG_CONTAINER", "mcp-dev-postgres")
ADMIN_PW = os.environ.get("MCP_TEST_PG_ADMIN_PW", "mcp_admin_dev_password")
RO_PW = os.environ.get("MCP_TEST_PG_RO_PW", "mcp_query_ro_dev_password")

# -- SOURCED demo corpus (team-visible). Each doc gets a resolvable source_uri + a '*team*' grant,
#    so it survives permission choke point #1 and can be graded FACT by choke point #2. ----------
DOCS = [
    (
        "confluence", "c1", "https://wiki.example.test/PAY/payment-retry",
        "Payment retry policy", "PAY",
        "The payment worker retries failed transactions three times with exponential backoff "
        "before moving the message to the dead letter queue payment-retry-dlq.",
    ),
    (
        "gitlab", "g1", "https://gitlab.example.test/payments/worker/README",
        "worker README", "payments/worker",
        "The payments worker handles ERR_PAYMENT_TIMEOUT and retries three times in the release "
        "pipeline before alerting.",
    ),
]


def _host_port() -> int:
    out = subprocess.run(
        ["docker", "port", PG_CONTAINER, "5432/tcp"], capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"container {PG_CONTAINER} does not publish 5432 (docker compose up -d?)")
    return int(out.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _admin_dsn(port: int, database: str) -> str:
    return f"postgresql://mcp_admin:{ADMIN_PW}@127.0.0.1:{port}/{database}"


def _create_db(port: int) -> str:
    import psycopg

    name = f"t_t110_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return name


def _drop_db(port: int, name: str) -> None:
    import psycopg

    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _make_ro_login(port: int, database: str, suffix: str) -> str:
    """Create a THROW-AWAY login role that inherits `mcp_query_ro` and return its DSN.

    This avoids changing the shared dev role's password: the smoke connects as a fresh role that is
    a member of `mcp_query_ro` (so it has exactly the read-only grants and nothing more) and is
    dropped at teardown. The real `mcp_query_ro` / `mcp_ingest_rw` roles are untouched.
    """
    import psycopg
    from psycopg import sql

    role = f"t110_ro_{suffix}"
    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE mcp_query_ro").format(
                sql.Identifier(role), sql.Literal(RO_PW)
            )
        )
        # mirror migration 0005's read-only role settings on mcp_query_ro: the startup credential
        # check refuses to serve unless default_transaction_read_only=on (ADR-0003 A1).
        conn.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = '15s'").format(sql.Identifier(role))
        )
        # the inherited role must be allowed to connect to and use the throw-away DB
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
    return f"postgresql://{role}:{RO_PW}@127.0.0.1:{port}/{database}"


def _drop_role(port: int, role: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(_admin_dsn(port, "postgres"), autocommit=True) as conn:
        with __import__("contextlib").suppress(psycopg.Error):
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _migrate_only(port: int, database: str) -> None:
    """Apply the full migration chain but seed NO documents — the honest 'no source' corpus."""
    import psycopg

    with psycopg.connect(_admin_dsn(port, database), autocommit=True) as conn:
        upgrade(conn)


def _seed(port: int, database: str) -> None:
    import psycopg

    with psycopg.connect(_admin_dsn(port, database), autocommit=True) as conn:
        upgrade(conn)  # real full chain incl 0007 / 0007b (content_tsv + GIN) / 0008
        run_id = conn.execute(
            "INSERT INTO kb.ingest_runs (source_type, status) VALUES ('confluence','success') "
            "RETURNING id"
        ).fetchone()[0]
        for source_type, source_id, uri, title, container, text in DOCS:
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
            # permission choke point #1: team-visible grant so the read survives default-deny.
            conn.execute(
                "INSERT INTO kb.document_permissions (document_id, principal, grant_type) "
                "VALUES (%s, '*team*', 'read')",
                (doc_id,),
            )


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
            # read-only role (mcp_ingest_rw would be refused at startup, ADR-0003 A1)
            "MCP_KNOWLEDGE_DSN": ro_dsn,
            "MCP_INGEST_EMBEDDING_PROVIDER": "http",
            "MCP_INGEST_EMBEDDING_URL": embed_url + "/v1",
            "MCP_INGEST_EMBEDDING_MODEL": MODEL,
            "MCP_INGEST_EMBEDDING_DIMENSIONS": str(DIM),
            # grounding/rerank must not reach the network (NFR-003/NFR-011): offline + RRF-only.
            "MCP_KNOWLEDGE_RERANKER_ENABLED": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "MCP_ALLOW_UNVERIFIED_CREDENTIALS": "true",
        }
    )
    return env


async def _drive(
    ro_dsn: str, embed_url: str, query: str, *, probe_write: bool
) -> dict[str, object]:
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
                out["tools"] = sorted(t.name for t in (await session.list_tools()).tools)
                res = await session.call_tool("search_company_knowledge", {"query": query})
                out["result"] = {
                    "isError": bool(res.isError),
                    "structuredContent": res.structuredContent,
                }
                if probe_write:
                    # negative: a write tool must not exist on the read-only surface.
                    try:
                        deletion = await session.call_tool("kb_delete_document", {"id": "x"})
                        out["write_probe"] = {
                            "isError": bool(deletion.isError),
                            "structuredContent": deletion.structuredContent,
                        }
                    except Exception as exc:  # noqa: BLE001
                        out["write_probe"] = {"rejected_at_protocol": repr(exc)}
    return out


def _verdict_summary(payload: dict | None) -> dict[str, object]:
    if not payload:
        return {}
    gs = payload.get("grounding_summary") or {}
    return {
        "status": payload.get("status"),
        "fact": gs.get("fact"),
        "low_confidence": gs.get("low_confidence"),
        "unknown": gs.get("unknown"),
        "conflict": gs.get("conflict"),
        "calibration_status": gs.get("calibration_status"),
        "reranker": gs.get("reranker"),
        "n_claims": len(payload.get("claims") or []),
        "n_citations": len(payload.get("citations") or []),
        "first_claim_verdict": (payload.get("claims") or [{}])[0].get("grounding"),
        "first_claim_message": (payload.get("claims") or [{}])[0].get("message"),
    }


def main() -> int:
    port = _host_port()
    embed_srv, embed_url = _start_embed_stub()
    # Scenario A: migrated DB seeded with sourced, team-granted docs  -> FACT.
    # Scenario B: migrated DB with an EMPTY corpus (no documents)      -> UNKNOWN.
    db_a = _create_db(port)
    db_b = _create_db(port)
    suf_a = db_a.rsplit("_", 1)[1]
    suf_b = db_b.rsplit("_", 1)[1]
    role_a = f"t110_ro_{suf_a}"
    role_b = f"t110_ro_{suf_b}"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    print(f"# CHG-001 T-110 grounding stdio smoke — {stamp}Z")
    print(f"# pgvector throw-away DBs in container {PG_CONTAINER}:{port} (live mcp_kb untouched)")
    print(f"#   A sourced={db_a}   B empty={db_b}")
    print(f"# embedding: DeterministicFakeProvider {MODEL} / {DIM}d over local http stub "
          f"(HF egress blocked, NFR-003 UNVERIFIED — relevance NOT measured)\n")
    try:
        # A — sourced corpus + '*team*' read grants (permission choke point #1 lets them through).
        _seed(port, db_a)
        ro_a = _make_ro_login(port, db_a, suf_a)
        # B — migrate only; leave kb.documents / kb.chunks EMPTY (the honest "no source" state).
        _migrate_only(port, db_b)
        ro_b = _make_ro_login(port, db_b, suf_b)
        print("migrations: real upgrade() applied to BOTH DBs (chain incl 0007 / 0007b "
              "content_tsv+GIN / 0008)")
        print(f"A seeded: {len(DOCS)} sourced docs + '*team*' read grants")
        print("B seeded: nothing (empty corpus)")
        print(f"read roles: throw-away {role_a} / {role_b} (members of mcp_query_ro; "
              "real roles untouched)\n")

        q_sourced = "how many times does the payment worker retry failed transactions"
        q_unknown = "quarterly revenue of the Antarctic penguin division in 1812"
        a = asyncio.run(_drive(ro_a, embed_url, q_sourced, probe_write=True))
        b = asyncio.run(_drive(ro_b, embed_url, q_unknown, probe_write=False))

        print("--- mcp-knowledge tools/list (read-only surface) ---")
        print(json.dumps(a["tools"], indent=2))

        sourced = a["result"]["structuredContent"]  # type: ignore[index]
        unknown = b["result"]["structuredContent"]  # type: ignore[index]

        print(f"\n--- A) SOURCED query: {q_sourced!r} ---")
        print("verdict summary:", json.dumps(_verdict_summary(sourced), ensure_ascii=False))
        print(json.dumps(sourced, indent=2, ensure_ascii=False)[:2600])

        print(f"\n--- B) NO-SOURCE query (empty corpus): {q_unknown!r} ---")
        print("verdict summary:", json.dumps(_verdict_summary(unknown), ensure_ascii=False))
        print(json.dumps(unknown, indent=2, ensure_ascii=False)[:1800])

        print("\n--- C) write probe kb_delete_document (must not exist / be refused) ---")
        print(json.dumps(a["write_probe"], indent=2, ensure_ascii=False)[:900])

        # Assert the two invariants the smoke exists to prove (verdict enum renders UPPERCASE).
        s = _verdict_summary(sourced)
        u = _verdict_summary(unknown)
        ok_fact = (
            s["status"] == "ok"
            and (s["fact"] or 0) >= 1
            and s["first_claim_verdict"] == "FACT"
            and (s["n_citations"] or 0) >= 1
        )
        ok_unknown = (
            u["status"] == "insufficient_evidence"
            and u["first_claim_verdict"] == "UNKNOWN"
            and (u["fact"] or 0) == 0
            and (u["n_citations"] or 0) == 0
        )
        ok_write = bool((a.get("write_probe") or {}).get("isError"))
        print("\n=== RESULT ===")
        print(f"A sourced  -> FACT    : {'PASS' if ok_fact else 'FAIL'}  "
              f"(status={s['status']}, fact={s['fact']}, citations={s['n_citations']})")
        print(f"B no-source-> UNKNOWN : {'PASS' if ok_unknown else 'FAIL'}  "
              f"(status={u['status']}, first={u['first_claim_verdict']}, fact={u['fact']})")
        print(f"C write tool refused  : {'PASS' if ok_write else 'FAIL'}")
        return 0 if (ok_fact and ok_unknown and ok_write) else 1
    finally:
        embed_srv.shutdown()
        for db, role in ((db_a, role_a), (db_b, role_b)):
            _drop_db(port, db)
            _drop_role(port, role)
        print(f"\n# throw-away DBs {db_a} / {db_b} + roles {role_a} / {role_b} dropped")


if __name__ == "__main__":
    sys.exit(main())
