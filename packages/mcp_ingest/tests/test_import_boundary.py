"""T-058 / R19 / ADR-0010: `mcp_ingest.embedding` and `mcp_ingest.ports` must not import
`psycopg` (directly or transitively), so the read-only `mcp-pgvector` server can reuse them
without pulling in the write path.

Two independent checks: a static scan of the import graph, and a real interpreter that imports
the modules and inspects `sys.modules`.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_ingest

PKG = Path(mcp_ingest.__file__).parent
FORBIDDEN_ROOTS = {"psycopg", "psycopg_pool", "psycopg2", "asyncpg"}
FORBIDDEN_INTERNAL = {"mcp_ingest.db", "mcp_ingest.cli", "mcp_ingest.migrate"}


def _module_path(name: str) -> Path | None:
    parts = name.split(".")[1:]
    if not parts:
        return PKG / "__init__.py"
    rel = PKG.joinpath(*parts)
    for candidate in (rel.with_suffix(".py"), rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _closure(start: str) -> set[str]:
    seen: set[str] = set()
    todo = [start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_path(name) if name.startswith("mcp_ingest") else None
        if path is None:
            continue
        for imported in _imports(path):
            if imported.startswith("mcp_ingest"):
                todo.append(imported)
            else:
                seen.add(imported)
    return seen


@pytest.mark.parametrize("entry", ["mcp_ingest", "mcp_ingest.ports", "mcp_ingest.embedding"])
def test_R19_static_import_graph_has_no_psycopg(entry: str) -> None:
    reachable = _closure(entry)
    roots = {name.split(".")[0] for name in reachable}
    assert not roots & FORBIDDEN_ROOTS, sorted(roots & FORBIDDEN_ROOTS)
    assert not reachable & FORBIDDEN_INTERNAL, sorted(reachable & FORBIDDEN_INTERNAL)


def test_R19_every_embedding_module_is_free_of_psycopg() -> None:
    for path in sorted((PKG / "embedding").glob("*.py")):
        roots = {name.split(".")[0] for name in _imports(path)}
        assert not roots & FORBIDDEN_ROOTS, f"{path.name} imports {roots & FORBIDDEN_ROOTS}"


def test_R19_a_real_interpreter_importing_the_embedding_stack_never_loads_psycopg() -> None:
    code = (
        "import sys\n"
        "import mcp_ingest, mcp_ingest.ports\n"
        "from mcp_ingest.embedding import EmbeddingSettings, build_provider\n"
        "import mcp_ingest.embedding.local, mcp_ingest.embedding.http, mcp_ingest.embedding.fake\n"
        "build_provider(EmbeddingSettings(provider='local'))\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        f"{sorted(FORBIDDEN_ROOTS)!r})\n"
        "sys.exit(1 if bad else 0)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_R19_the_boundary_check_would_catch_a_violation(tmp_path: Path) -> None:
    offender = tmp_path / "bad.py"
    offender.write_text("import psycopg\nfrom psycopg import sql\n", encoding="utf-8")
    assert {n.split(".")[0] for n in _imports(offender)} & FORBIDDEN_ROOTS
