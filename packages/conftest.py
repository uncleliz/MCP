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


@pytest.fixture(autouse=True)
def _isolate_registered_secrets() -> Iterator[None]:
    """Keep the value-based secret registry (`mcp_common.redact`) isolated per test.

    E-mcp-data-platform-009 wires `register_secret()`/`register_dsn_secret()` into every
    source's client-construction seam, so simply constructing a client in a test now adds the
    configured credential to the process-global `_REGISTERED_SECRETS`. Clearing it before and
    after each test stops one test's registered secret from scrubbing another test's output
    (and protects `test_token_never_leaks`'s own assertions). Idempotent and cheap.
    """
    from mcp_common.redact import clear_registered_secrets

    clear_registered_secrets()
    yield
    clear_registered_secrets()


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


# -- Docker pgvector (the real go-live store engine) for CHG-001 DK2 migration-locking tests ---
#
# The distro-binary cluster above cannot build an HNSW index unless pgvector is installed next to
# those binaries (it usually is not on a dev laptop). The dev compose stack already runs
# `pgvector/pgvector:pg16`, which is the exact engine the kb store went live on, so the CHG-001
# migration-locking tests (T-087/T-088) provision a throw-away *database* inside that container
# and never touch the live `mcp_kb` database. Skipped, with a reason, when Docker or the container
# is not available — so `make ci` stays green on a machine without Docker.

_DOCKER_PG_CONTAINER = os.environ.get("MCP_TEST_PG_CONTAINER", "mcp-dev-postgres")
_DOCKER_PG_USER = os.environ.get("MCP_TEST_PG_USER", "mcp_admin")


@dataclass(frozen=True)
class DockerPg:
    container: str
    user: str
    host_port: int

    def dsn(self, database: str) -> str:
        return f"postgresql://{self.user}@127.0.0.1:{self.host_port}/{database}"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_running(name: str) -> bool:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=15,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _published_port(name: str) -> int | None:
    try:
        out = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            capture_output=True, text=True, timeout=15,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    # e.g. "0.0.0.0:5433\n[::]:5433"
    first = out.stdout.strip().splitlines()[0]
    try:
        return int(first.rsplit(":", 1)[1])
    except (IndexError, ValueError):  # pragma: no cover - env
        return None


@pytest.fixture(scope="session")
def docker_pgvector() -> DockerPg:
    """The running compose pgvector container (the real kb store engine), or skip."""
    if not _docker_available():
        pytest.skip("docker not available; CHG-001 migration-locking tests need pgvector")
    if not _container_running(_DOCKER_PG_CONTAINER):
        pytest.skip(f"container {_DOCKER_PG_CONTAINER} is not running (docker compose up -d)")
    port = _published_port(_DOCKER_PG_CONTAINER)
    if port is None:
        pytest.skip(f"container {_DOCKER_PG_CONTAINER} does not publish 5432")
    import psycopg

    server = DockerPg(container=_DOCKER_PG_CONTAINER, user=_DOCKER_PG_USER, host_port=port)
    try:  # confirm reachable + pgvector present, else skip (never fail)
        with psycopg.connect(server.dsn("postgres"), connect_timeout=5, autocommit=True) as conn:
            row = conn.execute(
                "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
            ).fetchone()
            has_vector = bool(row and row[0])
    except psycopg.Error as exc:  # pragma: no cover - env
        pytest.skip(f"cannot reach {_DOCKER_PG_CONTAINER}: {type(exc).__name__}")
    if not has_vector:  # pragma: no cover - env
        pytest.skip("pgvector not available in the container")
    return server


@pytest.fixture
def docker_pg_factory(docker_pgvector: DockerPg) -> Iterator[Callable[[], str]]:
    """`factory() -> admin DSN` of a fresh throw-away database in the pgvector container.

    Each database is dropped on teardown; the live `mcp_kb` database is never touched.
    """
    import psycopg

    created: list[str] = []

    def factory() -> str:
        name = f"t_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(docker_pgvector.dsn("postgres"), autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return docker_pgvector.dsn(name)

    yield factory
    with psycopg.connect(docker_pgvector.dsn("postgres"), autocommit=True) as conn:
        for name in created:
            for _ in range(5):
                try:
                    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                    break
                except psycopg.Error:  # pragma: no cover - transient
                    time.sleep(0.2)
