"""T-024/T-025: read_api for project & code tools (search_projects, search_code, get_file,
list_repository_tree). AC: FR-002/AC-001, FR-002/AC-002, FR-002/AC-003, FR-015/AC-001."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from gitlab_helpers import API, BASE, PROJ, json_response, load_fixture
from mcp_common.config import CommonSettings
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.tooling import decode_cursor
from mcp_gitlab.read_api import GitLabReadApi

WEB = f"{BASE}/team/payment-service"


# ---- gitlab_search_projects -------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_001_search_projects(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/projects").mock(
        return_value=json_response("projects.json", headers={"X-Next-Page": "2"})
    )
    outcome = await read_api.search_projects(query="pay", membership_only=True, limit=2)
    result = outcome.result
    assert result.status.value == "ok" and len(result.items) == 2
    assert [c.uri for c in result.citations] == [WEB, f"{BASE}/team/ledger"]
    assert decode_cursor(result.meta.next_cursor, source="gitlab") == {"page": 2}
    params = route.calls.last.request.url.params
    assert params["membership"] == "true" and params["per_page"] == "2" and params["page"] == "1"


@pytest.mark.asyncio
async def test_FR_002_AC_002_search_projects_empty_is_status(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[]))
    outcome = await read_api.search_projects(query="zzz")
    assert outcome.result.status.value == "empty"
    assert outcome.result.meta.query_echo["query"] == "zzz"
    assert "GitLab" in (outcome.query_description or "")


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"query": ""}, "query"),
        ({"query": "x" * 257}, "query"),
        ({"query": "x", "limit": 0}, "limit"),
        ({"query": "x", "limit": 101}, "limit"),
        ({"query": "x", "cursor": "%%%"}, "cursor"),
    ],
)
@pytest.mark.asyncio
async def test_search_projects_bounds(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter, kwargs: dict, field: str
) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.search_projects(**kwargs)
    assert exc.value.code == ErrorCode.INVALID_INPUT and exc.value.details["field"] == field
    assert readonly_respx_router.calls.call_count == 0


# ---- gitlab_search_code -----------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_001_search_code_global_with_web_citations_and_denied_hit_skipped(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/search").mock(return_value=json_response("search_blobs.json"))
    outcome = await read_api.search_code(query="retry")
    result = outcome.result
    assert result.status.value == "ok"
    assert [i["path"] for i in result.items] == ["src/retry.py", "docs/notes.md"]
    assert [i["project_path"] for i in result.items] == ["team/payment-service", "team/ledger"]
    assert result.citations[0].uri == f"{WEB}/-/blob/main/src/retry.py#L12"
    assert all("/api/v4" not in (c.uri or "") for c in result.citations)
    assert result.items[0]["excerpt"].startswith('<untrusted-content source="gitlab"')
    assert any("deny-glob" in w for w in result.meta.warnings)
    assert "config/prod.env" not in str(result.items)
    assert "hunter2" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_search_code_project_scope_passes_ref_and_uses_project_endpoint(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/search").mock(
        return_value=json_response("search_blobs.json")
    )
    await read_api.search_code(query="retry", project="team/payment-service", ref="develop")
    params = route.calls.last.request.url.params
    assert params["scope"] == "blobs" and params["search"] == "retry" and params["ref"] == "develop"


@pytest.mark.asyncio
async def test_search_code_filename_filter(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/search").mock(return_value=json_response("search_blobs.json"))
    outcome = await read_api.search_code(query="retry", filename_filter="*.md")
    assert [i["path"] for i in outcome.result.items] == ["docs/notes.md"]


@pytest.mark.asyncio
async def test_search_code_redacts_secrets_in_excerpt(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    hit = {
        **load_fixture("search_blobs.json")[0],
        "data": "TOKEN glpat-abcdefghijklmnopqrstuvwxyz01",
    }
    gitlab.get(f"{API}/search").mock(return_value=httpx.Response(200, json=[hit]))
    outcome = await read_api.search_code(query="retry")
    assert "glpat-" not in outcome.result.items[0]["excerpt"]
    assert outcome.result.meta.redactions == 1


@pytest.mark.asyncio
async def test_FR_002_AC_002_search_code_empty_and_all_denied_are_empty(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(f"{API}/search").mock(return_value=httpx.Response(200, json=[]))
    assert (await read_api.search_code(query="nothing")).result.status.value == "empty"
    only_denied = [load_fixture("search_blobs.json")[1]]
    gitlab.get(f"{API}/search").mock(return_value=httpx.Response(200, json=only_denied))
    outcome = await read_api.search_code(query="password")
    assert outcome.result.status.value == "empty"


@pytest.mark.asyncio
async def test_FR_002_AC_002_search_code_unknown_project_is_not_found(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(url__regex=r".*/projects/.*").mock(
        return_value=json_response("error_404.json", status=404)
    )
    outcome = await read_api.search_code(query="retry", project="no/such")
    assert outcome.result.status.value == "not_found"


@pytest.mark.asyncio
async def test_search_code_skips_hit_whose_project_lookup_fails(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/search").mock(return_value=json_response("search_blobs.json"))
    readonly_respx_router.get(f"{API}/projects/42").mock(
        return_value=json_response("project_42.json")
    )
    readonly_respx_router.get(f"{API}/projects/43").mock(return_value=httpx.Response(403, json={}))
    outcome = await read_api.search_code(query="retry")
    assert [i["path"] for i in outcome.result.items] == ["src/retry.py"]
    assert any("project 43" in w for w in outcome.result.meta.warnings)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"query": "x"}, "query"),
        ({"query": "xx", "project": "p" * 513}, "project"),
        ({"query": "xx", "limit": 101}, "limit"),
    ],
)
@pytest.mark.asyncio
async def test_search_code_bounds(read_api: GitLabReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.search_code(**kwargs)
    assert exc.value.details["field"] == field


# ---- gitlab_get_file --------------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_001_get_file_decodes_wraps_and_links_to_web(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    route = gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_retry.json")
    )
    outcome = await read_api.get_file(
        project="team/payment-service", path="src/retry.py", ref="HEAD"
    )
    item = outcome.result.items[0]
    assert route.calls.last.request.url.params["ref"] == "main"  # HEAD -> default branch
    assert item["content"].startswith('<untrusted-content source="gitlab"')
    assert "def retry_payment" in item["content"]
    assert item["web_url"] == f"{WEB}/-/blob/main/src/retry.py"
    assert item["project_path"] == "team/payment-service" and item["truncated"] is False
    assert outcome.result.citations[0].locator["path"] == "src/retry.py"


@pytest.mark.asyncio
async def test_FR_002_AC_003_get_file_on_denied_path_is_not_permitted(
    read_api: GitLabReadApi, readonly_respx_router: respx.MockRouter
) -> None:
    for path in (".env", "certs/server.pem", "config/prod.env"):
        with pytest.raises(NotPermittedError):
            await read_api.get_file(project="42", path=path)
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_get_file_redacts_secret_content(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_secret.json")
    )
    outcome = await read_api.get_file(project="42", path="src/deploy.py", ref="main")
    assert "AKIA" not in outcome.result.items[0]["content"]
    assert outcome.result.meta.redactions >= 1


@pytest.mark.asyncio
async def test_get_file_binary_is_omitted(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_binary.json")
    )
    outcome = await read_api.get_file(project="42", path="img/logo.png", ref="main")
    assert "binary file omitted" in outcome.result.items[0]["content"]
    assert any("nhị phân" in w for w in outcome.result.meta.warnings)


@pytest.mark.asyncio
async def test_get_file_truncates_at_max_bytes_and_is_partial(
    read_api: GitLabReadApi, gitlab: respx.MockRouter
) -> None:
    gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_big.json")
    )
    outcome = await read_api.get_file(project="42", path="src/big.txt", ref="main", max_bytes=1024)
    result = outcome.result
    assert result.status.value == "partial" and result.meta.truncated
    assert result.items[0]["truncated"] is True and result.citations


@pytest.mark.asyncio
async def test_get_file_max_bytes_clamped_to_server_cap_with_warning(
    client, gitlab: respx.MockRouter
) -> None:
    api = GitLabReadApi(client, CommonSettings(max_output_bytes=2048))
    gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_big.json")
    )
    outcome = await api.get_file(project="42", path="src/big.txt", ref="main", max_bytes=65536)
    assert any("clamped" in w for w in outcome.result.meta.warnings)
    assert len(outcome.result.items[0]["content"].encode()) < 2048 + 400  # cap + wrapper text


@pytest.mark.asyncio
async def test_FR_002_AC_002_get_file_missing_file_or_project_is_not_found(
    read_api: GitLabReadApi, gitlab: respx.MockRouter, readonly_respx_router: respx.MockRouter
) -> None:
    gitlab.get(url__regex=r".*/repository/files/.*").mock(
        return_value=httpx.Response(404, json={"message": "404 File Not Found"})
    )
    outcome = await read_api.get_file(project="42", path="nope.py", ref="main")
    assert outcome.result.status.value == "not_found"
    assert outcome.identifier == "42:nope.py@main"

    readonly_respx_router.get(f"{API}/projects/ghost").mock(
        return_value=json_response("error_404.json", status=404)
    )
    outcome = await read_api.get_file(project="ghost", path="a.py", ref="main")
    assert outcome.result.status.value == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"project": "42", "path": ""}, "path"),
        ({"project": "42", "path": "x" * 1025}, "path"),
        ({"project": "42", "path": "a", "max_bytes": 10}, "max_bytes"),
        ({"project": "42", "path": "a", "max_bytes": 999999}, "max_bytes"),
        ({"project": "", "path": "a"}, "project"),
    ],
)
@pytest.mark.asyncio
async def test_get_file_bounds(read_api: GitLabReadApi, kwargs: dict, field: str) -> None:
    with pytest.raises(ToolError) as exc:
        await read_api.get_file(**kwargs)
    assert exc.value.details["field"] == field


# ---- gitlab_list_repository_tree ---------------------------------------------------


@pytest.mark.asyncio
async def test_list_repository_tree(read_api: GitLabReadApi, gitlab: respx.MockRouter) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/repository/tree").mock(
        return_value=json_response("tree.json")
    )
    outcome = await read_api.list_repository_tree(
        project="team/payment-service", path="", recursive=True, limit=10
    )
    names = [i["name"] for i in outcome.result.items]
    assert names == ["src", "README.md", "vendor"]
    params = route.calls.last.request.url.params
    assert params["recursive"] == "true" and "path" not in params and params["ref"] == "main"
    assert outcome.result.items[0]["web_url"] == f"{WEB}/-/tree/main/src"


@pytest.mark.asyncio
async def test_tree_empty_and_not_found(read_api: GitLabReadApi, gitlab: respx.MockRouter) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/repository/tree")
    route.mock(return_value=httpx.Response(200, json=[]))
    assert (
        await read_api.list_repository_tree(project="team/payment-service")
    ).result.status.value == "empty"
    route.mock(return_value=httpx.Response(404, json={"message": "404 Tree Not Found"}))
    outcome = await read_api.list_repository_tree(project="team/payment-service", path="zzz")
    assert outcome.result.status.value == "not_found"


@pytest.mark.asyncio
async def test_tree_explicit_ref_is_used(read_api: GitLabReadApi, gitlab: respx.MockRouter) -> None:
    route = gitlab.get(f"{API}/projects/{PROJ}/repository/tree").mock(
        return_value=json_response("tree.json")
    )
    await read_api.list_repository_tree(project="team/payment-service", ref="v1.2")
    assert route.calls.last.request.url.params["ref"] == "v1.2"


def test_binary_fixture_really_has_nul() -> None:
    raw = base64.b64decode(load_fixture("file_binary.json")["content"])
    assert b"\x00" in raw
