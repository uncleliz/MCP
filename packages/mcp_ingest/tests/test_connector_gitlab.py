"""T-071: GitLab connector over the real `mcp_gitlab.client` with a mocked HTTP layer.

AC: FR-012/AC-001, BR-005, R4. Fixtures follow the GitLab REST API v4 docs (no live instance
reachable, spike S1).
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.errors import NotPermittedError
from mcp_gitlab.client import ALLOWED_OPERATIONS, GitLabClient
from mcp_gitlab.settings import Settings as GitLabSettings
from mcp_ingest.connectors import gitlab as module
from mcp_ingest.connectors.base import Cursor
from mcp_ingest.connectors.gitlab import GitLabConnector
from mcp_ingest.settings import Settings
from pydantic import SecretStr

BASE = "https://gitlab.example.com"
API = f"{BASE}/api/v4"


def project(
    pid: int = 42,
    path: str = "payments/worker",
    *,
    visibility: str = "internal",
    repo: str = "enabled",
    issues: str = "enabled",
    mrs: str = "enabled",
    level: int | None = 30,
    activity: str = "2026-09-30T10:00:00.000Z",
    branch: str | None = "main",
) -> dict[str, Any]:
    return {
        "id": pid, "path_with_namespace": path, "web_url": f"{BASE}/{path}", "visibility": visibility,  # noqa: E501
        "default_branch": branch, "last_activity_at": activity,
        "repository_access_level": repo, "issues_access_level": issues,
        "merge_requests_access_level": mrs,
        "permissions": {"project_access": {"access_level": level} if level else None},
    }  # fmt: skip


def file_payload(text: str, size: int | None = None) -> dict[str, Any]:
    raw = text.encode()
    return {
        "encoding": "base64", "content": base64.b64encode(raw).decode(),
        "size": len(raw) if size is None else size, "blob_id": "abc123",
    }  # fmt: skip


class Fake:
    """Routes for one GitLab instance."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.files: dict[str, str] = {}
        self.fetched_paths: list[str] = []

    def project(self, data: dict[str, Any], tree: list[str] | None = None) -> None:
        pid = data["id"]
        self.router.get(f"{API}/projects/{pid}").mock(return_value=httpx.Response(200, json=data))
        entries = [{"type": "blob", "path": p} for p in tree or []]
        self.router.get(f"{API}/projects/{pid}/repository/tree").mock(
            return_value=httpx.Response(200, json=entries)
        )

        def file_handler(request: httpx.Request) -> httpx.Response:
            path = unquote(request.url.raw_path.decode().split("/files/", 1)[1].split("?", 1)[0])
            self.fetched_paths.append(path)
            if path not in self.files:
                return httpx.Response(404, json={"message": "404 File Not Found"})
            return httpx.Response(200, json=file_payload(self.files[path]))

        self.router.get(url__regex=rf"{re.escape(API)}/projects/{pid}/repository/files/.+").mock(
            side_effect=file_handler
        )

    def items(self, pid: int, kind: str, rows: list[dict[str, Any]]) -> respx.Route:
        return self.router.get(f"{API}/projects/{pid}/{kind}").mock(
            return_value=httpx.Response(200, json=rows)
        )


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def fake(router) -> Fake:
    return Fake(router)


def make(router, **kwargs: Any) -> GitLabConnector:
    client = GitLabClient(
        GitLabSettings(base_url=BASE, private_token=SecretStr("not-real")),
        common=CommonSettings(http_backoff_base=0.0),
    )
    defaults: dict[str, Any] = {
        "projects": ["42"],
        "team_projects": ["payments"],
        "file_globs": ["*.md", "*.txt"],
    }
    return GitLabConnector(client, **{**defaults, **kwargs})


def crawl(connector: GitLabConnector, cursor: Cursor | None = None, mode: str = "full"):
    try:
        return list(connector.iter_documents(cursor, mode))  # type: ignore[arg-type]
    finally:
        connector.close()


def test_FR_012_AC_001_files_become_documents_with_blob_urls(router, fake) -> None:
    fake.project(project(), tree=["README.md", "docs/design.md", "src/app.py"])
    fake.files = {"README.md": "# Worker\n\nretries", "docs/design.md": "Design notes"}
    docs = crawl(make(router, include_mrs_issues=False))
    by_id = {d.source_id: d for d in docs}
    assert set(by_id) == {"42:blob:README.md", "42:blob:docs/design.md"}  # *.py is not wanted
    readme = by_id["42:blob:README.md"]
    assert readme.source_uri == f"{BASE}/payments/worker/-/blob/main/README.md"
    assert readme.container == "payments/worker" and readme.title == "README.md"
    assert readme.source_format == "markdown" and "retries" in readme.raw_content
    assert by_id["42:blob:docs/design.md"].source_format == "markdown"
    assert readme.visibility == "team"


def test_R4_denied_paths_are_never_fetched_and_surface_as_blocked_documents(router, fake) -> None:
    fake.project(project(), tree=["README.md", ".env", "config/prod.env", "keys/server.pem",
                                  "docs/my-secret-notes.md", "id_rsa"])  # fmt: skip
    fake.files = {"README.md": "ok", ".env": "AWS=1", "keys/server.pem": "KEY"}
    docs = crawl(make(router, include_mrs_issues=False))
    blocked = {d.source_id for d in docs if d.blocked_reason}
    assert blocked == {
        "42:blob:.env", "42:blob:config/prod.env", "42:blob:keys/server.pem",
        "42:blob:docs/my-secret-notes.md", "42:blob:id_rsa",
    }  # fmt: skip
    assert all(d.raw_content == "" for d in docs if d.blocked_reason)
    assert fake.fetched_paths == ["README.md"], "denied files are not even requested"


def test_visibility_follows_the_s5_table(router, fake) -> None:
    cases = {
        "public": (project(1, "x/pub", visibility="public"), "team"),
        "internal": (project(2, "x/int", visibility="internal"), "team"),
        "private-declared": (project(3, "payments/priv", visibility="private", level=20), "team"),
        "private-undeclared": (
            project(4, "other/priv", visibility="private", level=40),
            "restricted",
        ),
        "private-guest": (
            project(5, "payments/guest", visibility="private", level=10),
            "restricted",
        ),
        "members-only-repo": (project(6, "x/mem", repo="private"), "restricted"),
    }
    for data, _ in cases.values():
        fake.project(data, tree=["README.md"])
    fake.files = {"README.md": "hello"}
    connector = make(router, projects=[str(d["id"]) for d, _ in cases.values()],
                     include_mrs_issues=False)  # fmt: skip
    docs = {d.container: d.visibility for d in crawl(connector)}
    assert docs == {data["path_with_namespace"]: expected for data, expected in cases.values()}


def test_internal_projects_can_be_declared_not_team(router, fake) -> None:
    fake.project(project(visibility="internal"), tree=["README.md"])
    fake.files = {"README.md": "hello"}
    docs = crawl(make(router, internal_is_team=False, include_mrs_issues=False))
    assert docs[0].visibility == "restricted"


def test_merge_requests_and_issues_use_their_web_url_and_confidential_is_restricted(
    router, fake
) -> None:
    fake.project(project(), tree=[])
    fake.items(42, "merge_requests", [{
        "iid": 7, "title": "Fix retry", "description": "Backoff bug", "web_url": f"{BASE}/payments/worker/-/merge_requests/7",  # noqa: E501
        "updated_at": "2026-09-30T11:00:00Z", "author": {"username": "an"}, "state": "merged",
    }])  # fmt: skip
    fake.items(42, "issues", [
        {"iid": 3, "title": "DLQ grows", "description": "details", "web_url": f"{BASE}/payments/worker/-/issues/3",  # noqa: E501
         "updated_at": "2026-09-30T12:00:00Z", "confidential": False},
        {"iid": 4, "title": "Salary leak", "description": "secret", "web_url": f"{BASE}/payments/worker/-/issues/4",  # noqa: E501
         "updated_at": "2026-09-30T12:30:00Z", "confidential": True},
    ])  # fmt: skip
    docs = {d.source_id: d for d in crawl(make(router))}
    assert docs["42:mr:7"].source_uri.endswith("/-/merge_requests/7")
    assert docs["42:mr:7"].author == "an" and "Backoff bug" in docs["42:mr:7"].raw_content
    assert docs["42:issue:3"].visibility == "team" and docs["42:issue:4"].visibility == "restricted"
    assert docs["42:mr:7"].source_updated_at is not None


def test_incremental_uses_updated_after_inclusive_and_skips_unchanged_projects(
    router, fake
) -> None:
    fake.project(project(activity="2026-09-30T10:00:00Z"), tree=["README.md"])
    fake.files = {"README.md": "hello"}
    row = lambda iid, when: {  # noqa: E731
        "iid": iid,
        "title": f"MR {iid}",
        "description": "",
        "web_url": f"{BASE}/p/-/merge_requests/{iid}",
        "updated_at": when,
    }
    route = fake.items(
        42,
        "merge_requests",
        [
            row(1, "2026-09-30T09:59:59Z"),
            row(2, "2026-09-30T10:00:00Z"),
            row(3, "2026-09-30T10:00:01Z"),
        ],
    )
    fake.items(42, "issues", [])
    cursor = Cursor(module.parse_ts("2026-09-30T10:00:00Z"))

    docs = crawl(make(router), cursor, "incremental")
    assert [d.source_id for d in docs if ":mr:" in d.source_id] == ["42:mr:2", "42:mr:3"]
    assert route.calls.last.request.url.params["updated_after"].startswith("2026-09-30T10:00:00")
    assert any(d.source_id.startswith("42:blob:") for d in docs)  # project activity == cursor

    fake.fetched_paths.clear()
    later = Cursor(module.parse_ts("2026-10-01T00:00:00Z"))
    docs = crawl(make(router), later, "incremental")
    assert not [d for d in docs if ":blob:" in d.source_id] and fake.fetched_paths == []


def test_oversized_files_and_empty_repositories_are_skipped(router, fake) -> None:
    fake.project(project(), tree=["big.md"])
    router.get(url__regex=rf"{re.escape(API)}/projects/42/repository/files/.+").mock(
        return_value=httpx.Response(200, json=file_payload("x", size=10_000_000))
    )
    assert crawl(make(router, include_mrs_issues=False)) == []
    fake.project(project(branch=None), tree=["README.md"])
    assert crawl(make(router, include_mrs_issues=False)) == []


def test_limit_and_pagination(router, fake) -> None:
    fake.project(project(), tree=[])
    pages = [
        httpx.Response(200, json=[{"type": "blob", "path": "a.md"}], headers={"X-Next-Page": "2"}),
        httpx.Response(200, json=[{"type": "blob", "path": "b.md"}, {"type": "tree", "path": "d"}]),
    ]
    router.get(f"{API}/projects/42/repository/tree").mock(side_effect=pages)
    fake.files = {"a.md": "A", "b.md": "B"}
    assert [d.title for d in crawl(make(router, include_mrs_issues=False))] == ["a.md", "b.md"]

    router.get(f"{API}/projects/42/repository/tree").mock(side_effect=pages)
    assert len(crawl_limit(make(router, include_mrs_issues=False), 1)) == 1


def crawl_limit(connector: GitLabConnector, limit: int):
    try:
        return list(connector.iter_documents(None, "full", limit=limit))
    finally:
        connector.close()


def test_fetch_documents_for_retry_failed(router, fake) -> None:
    fake.project(project(), tree=[])
    fake.files = {"docs/a.md": "alpha"}
    fake.router.get(f"{API}/projects/42/merge_requests/7").mock(
        return_value=httpx.Response(200, json={"iid": 7, "title": "T", "description": "d",
                                               "web_url": f"{BASE}/p/-/merge_requests/7", "updated_at": "2026-09-30T11:00:00Z"})  # noqa: E501
    )  # fmt: skip
    fake.router.get(f"{API}/projects/42/issues/9").mock(return_value=httpx.Response(404, json={}))
    connector = make(router)
    try:
        docs = list(
            connector.fetch_documents(
                ["42:blob:docs/a.md", "42:blob:.env", "42:mr:7", "42:issue:9", "42:blob:gone.md"]
            )
        )
    finally:
        connector.close()
    got = {d.source_id: d for d in docs}
    assert set(got) == {"42:blob:docs/a.md", "42:blob:.env", "42:mr:7"}
    assert got["42:blob:.env"].blocked_reason and got["42:blob:docs/a.md"].raw_content == "alpha"


def test_only_allowlisted_get_operations_are_used(router, fake) -> None:
    fake.project(project(), tree=["README.md"])
    fake.files = {"README.md": "x"}
    fake.items(42, "merge_requests", [])
    fake.items(42, "issues", [])
    crawl(make(router))
    assert {call.request.method for call in router.calls} == {"GET"}
    assert set(GitLabConnector.operations_used) <= set(ALLOWED_OPERATIONS)


def test_the_client_deny_glob_is_the_one_the_connector_relies_on(router) -> None:
    connector = make(router)
    try:
        with pytest.raises(NotPermittedError):
            connector._bridge.run(connector._client.get_file("42", ".env", "main"))
    finally:
        connector.close()


def test_status_and_build_follow_the_environment(monkeypatch) -> None:
    for name in ("MCP_GITLAB_BASE_URL", "MCP_GITLAB_PRIVATE_TOKEN", "MCP_INGEST_GITLAB_PROJECTS"):
        monkeypatch.delenv(name, raising=False)
    status = module.status(Settings())
    assert not status.configured and "MCP_INGEST_GITLAB_PROJECTS" in status.missing_env
    monkeypatch.setenv("MCP_GITLAB_BASE_URL", BASE)
    monkeypatch.setenv("MCP_GITLAB_PRIVATE_TOKEN", "not-real")
    monkeypatch.setenv("MCP_INGEST_GITLAB_PROJECTS", "payments/worker, 7")
    assert module.status(Settings()).configured
    built = module.build(Settings())
    try:
        assert built._projects == ["payments/worker", "7"]
    finally:
        built.close()
