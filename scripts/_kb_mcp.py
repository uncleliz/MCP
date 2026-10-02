#!/usr/bin/env python3
"""Read-only knowledge-base lookup over the real mcp-pgvector MCP server (stdio).

Shared driver for the CEO-facing CLI wrappers:
  * scripts/kb-list.sh    -> kb_list_sources   (no embedding)
  * scripts/kb-get.sh     -> kb_get_document   (no embedding)
  * scripts/kb-search.sh  -> kb_semantic_search (needs the SAME embedding as ingest)

Why a helper and not a one-liner: kb_semantic_search must embed the query in the *same* vector
space the chunks were stored in (ADR-0010). CHG-003 ingested the EA page with the deterministic
fake embedding served over a loopback HTTP endpoint (OpenAI-compatible), model "fake/hashed-bow",
1024-d, L2-normalised. The server's EmbeddingSettings provider is only `local|http` (there is no
`fake`), and the loopback endpoint is not a long-running service. So for a reproducible search we
start that identical endpoint here for the lifetime of the one query, point the read-only server
at it over stdio, run the query, then tear it down. Query vectors then match the stored vectors.

Honesty note (NFR-003): "fake/hashed-bow" is a hashed bag-of-words embedding — token overlap only,
no learned semantics. Ranking is therefore honest-but-unmeasured until the real BAAI/bge-m3 model
is downloaded and the corpus is re-embedded. The *rows and citations returned are the real stored
EA content*. We do not invent a recall number.

Everything is READ-ONLY: the DSN is the mcp_query_ro role; the write token is never used or read.
No secret value is printed by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
# DeterministicFakeProvider lives in the ingest package; make it importable without installing.
sys.path.insert(0, str(ROOT / "packages" / "mcp_ingest" / "src"))

# The exact embedding identity used at CHG-003 ingest. Changing any of these would make query
# vectors land in a different space and silently return nothing / wrong rows.
EMBED_MODEL = "fake/hashed-bow"
EMBED_DIM = 1024


def _start_embed_stub() -> tuple[ThreadingHTTPServer, str]:
    """Loopback OpenAI-compatible embedding endpoint serving the ingest-identical fake provider."""
    from mcp_ingest.embedding.fake import DeterministicFakeProvider

    provider = DeterministicFakeProvider(dimensions=EMBED_DIM, model_id=EMBED_MODEL)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if path.endswith("/embeddings"):
                texts = json.loads(body)["input"]
                vectors = provider.embed_documents(texts)
                payload = {"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}
                status = 200
            else:
                status, payload = 404, {}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:  # silence access log
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _server_env(embed_url: str | None) -> dict[str, str]:
    """Environment for `python -m mcp_pgvector serve`.

    Requires MCP_PGVECTOR_DSN (read-only role) to be already exported by the wrapper from
    credentials/.lookup.env. When an embedding is needed we point the server at the loopback
    stub as an http provider so it embeds exactly as ingest did.
    """
    env = dict(os.environ)
    dsn = env.get("MCP_PGVECTOR_DSN", "").strip()
    if not dsn:
        sys.stderr.write(
            "MCP_PGVECTOR_DSN is not set. Source credentials/.lookup.env first "
            "(the kb-*.sh wrappers do this for you).\n"
        )
        raise SystemExit(2)
    # Make the server's EmbeddingSettings match the stored chunks ("fake/hashed-bow", 1024-d).
    # The server's startup consistency check compares the configured model name against the stored
    # one (a string compare, no network call), so this must be set for *every* command — including
    # kb_list_sources / kb_get_document, which otherwise refuse to start on the default bge-m3.
    env["MCP_INGEST_EMBEDDING_PROVIDER"] = "http"
    env["MCP_INGEST_EMBEDDING_MODEL"] = EMBED_MODEL
    env["MCP_INGEST_EMBEDDING_DIMENSIONS"] = str(EMBED_DIM)
    # provider=http needs a URL even if the tool never embeds. For list/get we pass a loopback
    # placeholder that is never contacted; for search we pass the live stub so the query embeds.
    env["MCP_INGEST_EMBEDDING_URL"] = (embed_url or "http://127.0.0.1:1") + "/v1"
    return env


def _payload_from_result(result: object) -> dict:
    """Prefer the tool's structuredContent; else parse the first text block as JSON."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return dict(structured)
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", "") == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                return {"_text": block.text}
    return {}


async def _call_async(name: str, arguments: dict, env: dict[str, str]) -> dict:
    # Imported here so `--help` works without the mcp client installed.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_pgvector", "serve"], env=env, cwd=str(ROOT)
    )
    with open(os.devnull, "w") as errlog:  # the server logs to stderr by design
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    if getattr(result, "isError", False):
        detail = json.dumps(_payload_from_result(result), ensure_ascii=False)
        sys.stderr.write("MCP tool error: " + detail + "\n")
        raise SystemExit(1)
    return _payload_from_result(result)


def _call(name: str, arguments: dict, *, needs_embedding: bool) -> dict:
    stub = None
    embed_url = None
    try:
        if needs_embedding:
            stub, embed_url = _start_embed_stub()
        env = _server_env(embed_url)
        return asyncio.run(_call_async(name, arguments, env))
    finally:
        if stub is not None:
            stub.shutdown()


def _print_sources(payload: dict) -> None:
    items = payload.get("items", [])
    if not items:
        print("Chưa có nguồn nào trong knowledge base.")
        return
    print("Nguồn đã ingest trong knowledge base:")
    for it in items:
        print(
            f"  - {it.get('source_type')}: {it.get('document_count')} tài liệu, "
            f"{it.get('chunk_count')} chunk | model nhúng: {it.get('embedding_model')} "
            f"| cập nhật: {it.get('last_ingested_at')} "
            f"({it.get('staleness_hours')}h) | trạng thái: {it.get('last_run_status')}"
        )
    for cite in payload.get("citations", []):
        print(f"    nguồn gốc: {cite.get('uri')}  ({cite.get('label')})")


def _print_document(payload: dict) -> None:
    items = payload.get("items")
    doc = items[0] if items else (payload.get("document") or payload)
    title = doc.get("title") or "(không có tiêu đề)"
    uri = doc.get("source_uri") or doc.get("uri") or ""
    print(f"\nTài liệu: {title}")
    if uri:
        print(f"Nguồn: {uri}")
    print(f"source_id: {doc.get('source_id')}  |  document_id: {doc.get('document_id')}")
    if doc.get("container"):
        print(f"không gian (space): {doc.get('container')}  |  số chunk: {doc.get('chunk_count')}")
    if doc.get("source_updated_at"):
        updated = doc.get("source_updated_at")
        print(f"cập nhật nguồn: {updated}  |  ingested: {doc.get('ingested_at')}")
    body = (doc.get("content") or doc.get("text") or "").strip()
    if body:
        print("\n--- Nội dung ---")
        for line in body.splitlines():
            print(f"  {line}")
    if doc.get("truncated"):
        print("\n(Nội dung bị cắt bớt — truncated=true.)")
    for cite in payload.get("citations", []):
        print(f"\nTrích dẫn: {cite.get('uri')}")


def _print_hits(payload: dict) -> None:
    items = payload.get("items") or payload.get("results") or []
    if not items:
        print("Không có kết quả.")
        return
    for i, r in enumerate(items, 1):
        title = r.get("title") or "(không có tiêu đề)"
        uri = r.get("source_uri") or ""
        sim = r.get("similarity")
        head = r.get("heading_path") or ""
        text = (r.get("content") or r.get("text") or "").strip()
        print(f"\n[{i}] {title}")
        if sim is not None:
            print(f"    similarity: {sim}")
        if head:
            print(f"    mục: {head}")
        if uri:
            print(f"    nguồn: {uri}")
        if text:
            snippet = text if len(text) <= 500 else text[:500] + " …"
            for line in snippet.splitlines():
                print(f"    {line}")
    print(
        "\n(Lưu ý trung thực — NFR-003: embedding hiện là 'fake/hashed-bow' (chồng lặp từ, không "
        "có ngữ nghĩa học máy). Thứ hạng là THẬT-NHƯNG-CHƯA-ĐO cho tới khi tải model bge-m3 thật "
        "và re-embed. Nội dung và trích dẫn trả về là dữ liệu EA đã ingest thật.)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only knowledge-base lookup (mcp_query_ro).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="kb_list_sources (no embedding)")
    p_list.add_argument("--json", action="store_true")

    p_get = sub.add_parser("get", help="kb_get_document (no embedding)")
    g = p_get.add_mutually_exclusive_group(required=True)
    g.add_argument("--document-id")
    g.add_argument("--source-uri")
    p_get.add_argument("--json", action="store_true")

    p_search = sub.add_parser("search", help="kb_semantic_search (embeds like ingest)")
    p_search.add_argument("query")
    p_search.add_argument("top_k", nargs="?", type=int, default=5)
    p_search.add_argument("--min-similarity", type=float, default=0.0)
    p_search.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    def emit(payload: dict, printer) -> None:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            printer(payload)

    if args.cmd == "list":
        emit(_call("kb_list_sources", {}, needs_embedding=False), _print_sources)
        return 0
    if args.cmd == "get":
        arguments = (
            {"document_id": args.document_id}
            if args.document_id
            else {"source_uri": args.source_uri}
        )
        emit(_call("kb_get_document", arguments, needs_embedding=False), _print_document)
        return 0
    if args.cmd == "search":
        arguments = {
            "query": args.query,
            "top_k": args.top_k,
            "min_similarity": args.min_similarity,
        }
        emit(_call("kb_semantic_search", arguments, needs_embedding=True), _print_hits)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
