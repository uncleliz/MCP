"""T-062 / NFR-001: the closed SQL surface of mcp-pgvector contains no write or DDL, and nothing
in the package accepts SQL text from a caller (there is no `postgres_query(sql)` tool)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from mcp_pgvector.client import ALLOWED_STATEMENTS
from mcp_pgvector.sql import ALLOWED_GUCS, STATEMENTS

import mcp_pgvector

PACKAGE_DIR = Path(mcp_pgvector.__file__).parent
WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|copy|vacuum|reindex|"
    r"comment|lock|call|do|merge|listen|notify|refresh|cluster|discard|reset)\b",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _code(sql: str) -> str:
    """SQL without comments and without string literals (privilege names are literals)."""
    return re.sub(r"'[^']*'", "''", _strip_comments(sql))


def test_every_statement_is_a_select_or_show() -> None:
    for name, sql in STATEMENTS.items():
        first = _strip_comments(sql).strip().split(None, 1)[0].upper()
        assert first in {"SELECT", "SHOW", "WITH"}, (name, first)


def test_no_statement_contains_a_write_or_ddl_keyword() -> None:
    for name, sql in STATEMENTS.items():
        found = WRITE_KEYWORDS.findall(_code(sql))
        assert found == [], (name, found)


def test_every_statement_is_parameterised_never_interpolated() -> None:
    for name, sql in STATEMENTS.items():
        assert "{" not in sql and "'%s'" not in sql and "%s" not in sql.replace("%(", ""), name


def test_allowed_statements_are_exactly_the_registry() -> None:
    assert set(ALLOWED_STATEMENTS) == set(STATEMENTS)
    assert {"search", "search_unfiltered", "document_by_key", "document_chunks",
            "list_sources", "freshness"} <= set(STATEMENTS)  # fmt: skip


def test_session_tuning_is_limited_to_the_two_hnsw_settings() -> None:
    assert ALLOWED_GUCS == {"hnsw.ef_search", "hnsw.iterative_scan"}


def test_search_always_filters_tombstones_and_the_embedding_model() -> None:
    for name in ("search", "search_unfiltered"):
        sql = STATEMENTS[name]
        assert "d.deleted_at IS NULL" in sql and "c.embedding_model = %(model)s" in sql, name
        assert "ORDER BY c.embedding <=> %(query)s::vector" in sql, name
    for name in ("document_by_key", "list_sources", "freshness"):
        assert "deleted_at IS NULL" in STATEMENTS[name], name


def test_no_module_executes_sql_other_than_through_the_named_registry() -> None:
    """`.execute(` is only allowed on cursors/connections inside client.py's ReadTx/startup path,
    and always with `STATEMENTS[...]`; nothing else in the package may execute anything."""
    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"execute", "executemany", "copy"}:
                    ok = (
                        path.name == "client.py"
                        and node.args
                        and isinstance(node.args[0], ast.Subscript)
                        and isinstance(node.args[0].value, ast.Name)
                        and node.args[0].value.id == "STATEMENTS"
                    )
                    if not ok:
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_no_public_function_takes_sql_text() -> None:
    forbidden = {"sql", "query_sql", "statement", "raw_query"}
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = {a.arg for a in [*node.args.args, *node.args.kwonlyargs]}
                assert not args & forbidden, f"{path.name}:{node.name}"
