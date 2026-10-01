"""Workspace-wide pytest hooks.

`@pytest.mark.live` tests talk to a real upstream (Confluence Cloud, GitLab, OpenSearch, AWS, or
the Kafka/Redis containers of infra/docker-compose.yml) and need credentials + VPN or a running
Docker daemon, none of which exist in CI or the dev container. They are skipped
unless `MCP_LIVE_TESTS=1` is set, with an explicit reason (never a failure).
"""

from __future__ import annotations

import contextlib
import glob
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("MCP_LIVE_TESTS") == "1":
        return
    skip_live = pytest.mark.skip(
        reason=(
            "live integration test: needs real credentials/VPN or the Docker compose stack "
            "(set MCP_LIVE_TESTS=1 to run)"
        )
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# -- throw-away local PostgreSQL (+ pgvector) for the Phase 3 database tests -------------------
#
# Docker is usually unavailable, so tests that need a real Postgres start their own cluster from
# the distro binaries (`initdb`/`pg_ctl`, plus the `vector` extension files) on a random
# localhost port with trust auth, inside a temp dir that is deleted afterwards. They are skipped,
# with a reason, when the binaries or the extension are missing. The compose Postgres
# (infra/docker-compose.yml) remains the target of the `@pytest.mark.live` tests.


@dataclass(frozen=True)
class PgServer:
    port: int
    bin_dir: Path
    pgvector_version: str

    def dsn(self, database: str = "postgres", user: str = "postgres") -> str:
        return f"postgresql://{user}@127.0.0.1:{self.port}/{database}"


def _find_pg_bin() -> Path | None:
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True)
    return Path(candidates[0]).parent if candidates else None


def _as_postgres(cmd: list[str]) -> list[str]:
    """Postgres refuses to run as root; drop to the `postgres` user when we are root."""
    if os.geteuid() == 0:
        return ["runuser", "-u", "postgres", "--", *cmd]
    return cmd


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    bin_dir = _find_pg_bin()
    if bin_dir is None:
        pytest.skip("no local PostgreSQL server binaries (initdb) found; use the live tests")
    pg_config = bin_dir / "pg_config"
    try:
        share = subprocess.run(
            [str(pg_config), "--sharedir"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        share = ""
    if not share or not (Path(share) / "extension" / "vector.control").is_file():
        pytest.skip("pgvector extension files not installed (apt install postgresql-*-pgvector)")
    if os.geteuid() == 0:
        try:
            pwd.getpwnam("postgres")
        except KeyError:  # pragma: no cover - environment problem
            pytest.skip("no 'postgres' OS user to run the server as")
    root = Path(tempfile.mkdtemp(prefix="mcp-pg-"))
    root.chmod(0o755)
    if os.geteuid() == 0:
        shutil.chown(root, user="postgres")
    data = root / "data"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    try:
        subprocess.run(
            _as_postgres([str(bin_dir / "initdb"), "-D", str(data), "-U", "postgres",
                          "--auth=trust", "--encoding=UTF8", "--no-sync"]),
            check=True, capture_output=True,
        )  # fmt: skip
        options = (
            f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={root} "
            "-c fsync=off"
        )
        subprocess.run(
            _as_postgres([str(bin_dir / "pg_ctl"), "-D", str(data), "-w", "-t", "60",
                          "-l", str(root / "log"), "-o", options, "start"]),
            check=True, capture_output=True,
        )  # fmt: skip
    except (subprocess.CalledProcessError, OSError) as exc:  # pragma: no cover
        shutil.rmtree(root, ignore_errors=True)
        pytest.skip(f"could not start a local PostgreSQL: {exc}")
    import psycopg

    version = "unknown"
    try:
        admin = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        with psycopg.connect(admin, autocommit=True) as c:
            row = c.execute(
                "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
            ).fetchone()
            version = row[0] if row else "unknown"
        yield PgServer(port=port, bin_dir=bin_dir, pgvector_version=version)
    finally:
        with contextlib.suppress(Exception):
            subprocess.run(
                _as_postgres([str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"]),
                capture_output=True, timeout=60,
            )  # fmt: skip
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def pg_database_factory(pg_server: PgServer) -> Iterator[Callable[..., str]]:
    """`factory(template=None) -> admin DSN` of a fresh database, dropped on teardown."""
    import psycopg

    created: list[str] = []

    def factory(template: str | None = None) -> str:
        name = f"t_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
            suffix = f' TEMPLATE "{template}"' if template else ""
            conn.execute(f'CREATE DATABASE "{name}"{suffix}')
        created.append(name)
        return pg_server.dsn(name)

    yield factory
    with psycopg.connect(pg_server.dsn(), autocommit=True) as conn:
        for name in created:
            for _ in range(5):
                try:
                    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                    break
                except psycopg.Error:  # pragma: no cover - transient
                    time.sleep(0.2)
