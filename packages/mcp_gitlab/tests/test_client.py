"""T-023: GitLab `client.py` — allowlist, transport read-only, PAT scope startup check,
deny-glob (connector-facing layer). AC: FR-002/AC-003, FR-014/AC-001, NFR-002."""

from __future__ import annotations

import httpx
import pytest
import respx
from gitlab_helpers import API, BASE, json_response
from mcp_common.config import CommonSettings, SourceMisconfiguredError
from mcp_common.errors import ErrorCode, NotPermittedError, ToolError
from mcp_common.runtime import run_credential_check
from mcp_gitlab.client import ALLOWED_OPERATIONS, GitLabClient
from mcp_gitlab.settings import Settings
from pydantic import SecretStr

PAT_SELF = f"{API}/personal_access_tokens/self"


def test_allowlist_is_get_only() -> None:
    assert all(op.startswith("GET /api/v4/") for op in ALLOWED_OPERATIONS)
    assert "GET /api/v4/personal_access_tokens/self" in ALLOWED_OPERATIONS


@pytest.mark.asyncio
async def test_get_sends_private_token_header_and_reports_next_page(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(f"{API}/projects").mock(
        return_value=json_response("projects.json", headers={"X-Next-Page": "2"})
    )
    response = await client.search_projects("pay", membership=True, per_page=2, page=1)
    request = route.calls.last.request
    assert request.headers["private-token"] == "glpat-not-a-real-token-000000"
    assert request.url.params["search"] == "pay"
    assert request.url.params["membership"] == "true"
    assert request.url.params["per_page"] == "2"
    assert response.next_page == 2 and len(response.data) == 2


@pytest.mark.asyncio
async def test_no_next_page_header_means_none(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects").mock(return_value=json_response("projects.json"))
    assert (
        await client.search_projects("pay", membership=False, per_page=2, page=1)
    ).next_page is None


@pytest.mark.asyncio
async def test_FR_014_AC_001_operation_outside_allowlist_is_not_permitted(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get("GET /api/v4/projects/{id}/variables", {"id": "42"})
    assert exc.value.code == ErrorCode.NOT_PERMITTED
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_FR_014_AC_001_write_methods_blocked_by_transport_assertion(
    client: GitLabClient, readonly_respx_router: respx.MockRouter, method: str
) -> None:
    with pytest.raises(NotPermittedError):
        await client.http.request(method, f"{API}/projects/42/issues")
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_project_ref_with_slash_is_encoded(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(url__regex=r".*/projects/.*").mock(
        return_value=json_response("project_42.json")
    )
    await client.get_project("team/payment-service")
    assert str(route.calls.last.request.url).endswith("/api/v4/projects/team%2Fpayment-service")


@pytest.mark.asyncio
async def test_404_surfaces_upstream_status(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects/nope").mock(
        return_value=json_response("error_404.json", status=404)
    )
    with pytest.raises(ToolError) as exc:
        await client.get_project("nope")
    assert exc.value.details["upstream_status"] == 404


@pytest.mark.asyncio
async def test_non_json_is_upstream_error(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(f"{API}/projects/1").mock(
        return_value=httpx.Response(200, text="<html>sign in</html>")
    )
    with pytest.raises(ToolError) as exc:
        await client.get_project("1")
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


# ---- deny-glob --------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/prod.env",
        "certs/server.pem",
        "id_rsa",
        "home/.ssh/id_rsa.pub",
        "app/secrets.yml",
        "a/MY_CREDENTIALS.json",
    ],
)
def test_FR_002_AC_003_deny_glob_blocks_sensitive_paths(client: GitLabClient, path: str) -> None:
    assert client.is_path_denied(path)


@pytest.mark.parametrize("path", ["src/retry.py", "README.md", "docs/environment.md"])
def test_deny_glob_allows_ordinary_paths(client: GitLabClient, path: str) -> None:
    assert not client.is_path_denied(path)


# ---- R-002: deny-glob was too narrow (fnmatch-verified false negatives) ------------


@pytest.mark.parametrize(
    "path",
    [
        ".env.local",
        ".env.production",
        "config/.env.staging",
        "id_ed25519",
        "home/.ssh/id_ed25519.pub",
        "certs/server.key",
        "sa.p12",
        "client.pfx",
        "truststore.jks",
        ".npmrc",
        ".netrc",
        "terraform.tfstate",
        "env/terraform.tfstate.backup",
        "prod.tfvars",
    ],
)
def test_R_002_deny_glob_blocks_previously_missed_secret_filenames(
    client: GitLabClient, path: str
) -> None:
    assert client.is_path_denied(path)


@pytest.mark.asyncio
async def test_FR_002_AC_003_get_file_on_denied_path_never_hits_network(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    with pytest.raises(NotPermittedError) as exc:
        await client.get_file("42", ".env", "main")
    assert "MCP_GITLAB_PATH_DENY" in exc.value.message
    assert readonly_respx_router.calls.call_count == 0


@pytest.mark.asyncio
async def test_file_path_is_encoded_and_ref_passed(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    route = readonly_respx_router.get(url__regex=r".*/repository/files/.*").mock(
        return_value=json_response("file_retry.json")
    )
    await client.get_file("42", "src/retry.py", "main")
    request = route.calls.last.request
    assert "/repository/files/src%2Fretry.py" in str(request.url)
    assert request.url.params["ref"] == "main"


# ---- startup PAT scope check ------------------------------------------------------


@pytest.mark.asyncio
async def test_FR_002_AC_003_read_scopes_pass(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response("pat_self_ok.json"))
    report = await client.verify_credentials()
    assert report.ok, report.reasons
    assert await client.credential_check() is True


@pytest.mark.asyncio
async def test_read_api_only_scope_passes(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(PAT_SELF).mock(
        return_value=json_response("pat_self_read_api_only.json")
    )
    assert (await client.verify_credentials()).ok


@pytest.mark.parametrize(
    ("fixture_name", "needle"),
    [
        ("pat_self_api_scope.json", "api"),
        ("pat_self_write_repo.json", "write_repository"),
        ("pat_self_revoked.json", "revoked"),
        ("pat_self_no_scopes.json", "no scopes"),
    ],
)
@pytest.mark.asyncio
async def test_FR_002_AC_003_unsafe_token_fails_startup_check(
    client: GitLabClient, readonly_respx_router: respx.MockRouter, fixture_name: str, needle: str
) -> None:
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response(fixture_name))
    report = await client.verify_credentials()
    assert not report.ok
    assert needle in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_api_scope_token_refuses_to_serve(
    client: GitLabClient,
    readonly_respx_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_ALLOW_UNVERIFIED_CREDENTIALS", raising=False)
    readonly_respx_router.get(PAT_SELF).mock(return_value=json_response("pat_self_api_scope.json"))
    with pytest.raises(SourceMisconfiguredError):
        await run_credential_check(
            client.credential_check, settings=CommonSettings(), server_name="gitlab"
        )


@pytest.mark.asyncio
async def test_unauthorized_and_unreachable_fail_with_reason(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    readonly_respx_router.get(PAT_SELF).mock(return_value=httpx.Response(401, json={}))
    assert "unauthorized" in " ".join((await client.verify_credentials()).reasons)
    readonly_respx_router.get(PAT_SELF).mock(side_effect=httpx.ConnectError("refused"))
    report = await client.verify_credentials()
    assert not report.ok and "VPN" in " ".join(report.reasons)


@pytest.mark.asyncio
async def test_client_uses_settings_base_url(common: CommonSettings) -> None:
    other = GitLabClient(
        Settings(base_url="https://git.corp.test", private_token=SecretStr("x" * 12)), common=common
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://git.corp.test/api/v4/projects/1").mock(
            return_value=json_response("project_42.json")
        )
        await other.get_project("1")
        assert route.called
    assert BASE != "https://git.corp.test"


# ---- R-001: job-trace download has a hard byte cap --------------------------------


@pytest.mark.asyncio
async def test_get_job_trace_passes_through_when_within_cap(
    client: GitLabClient, readonly_respx_router: respx.MockRouter
) -> None:
    body = "line one\nline two\n"
    readonly_respx_router.get(f"{API}/projects/42/jobs/5001/trace").mock(
        return_value=httpx.Response(200, text=body)
    )
    trace = await client.get_job_trace("42", "5001")
    assert trace == body


@pytest.mark.asyncio
async def test_get_job_trace_over_cap_raises_response_too_large(
    common: CommonSettings, readonly_respx_router: respx.MockRouter
) -> None:
    settings = Settings(
        base_url=BASE,
        private_token=SecretStr("glpat-not-a-real-token-000000"),
        max_job_trace_bytes=16,
    )
    small_cap_client = GitLabClient(settings, common=common)
    big_body = "x" * 1024
    readonly_respx_router.get(f"{API}/projects/42/jobs/5001/trace").mock(
        return_value=httpx.Response(200, text=big_body)
    )

    with pytest.raises(ToolError) as exc:
        await small_cap_client.get_job_trace("42", "5001")

    assert exc.value.code == ErrorCode.RESPONSE_TOO_LARGE
    assert exc.value.details["max_bytes"] == 16
    assert readonly_respx_router.calls.call_count == 1


@pytest.mark.asyncio
async def test_get_job_trace_cap_enforced_even_if_content_length_lies(
    common: CommonSettings, readonly_respx_router: respx.MockRouter
) -> None:
    """A `Content-Length` header is upstream-controlled and may be wrong/absent
    (chunked transfer-encoding); the byte-counting fallback must catch it too."""
    settings = Settings(
        base_url=BASE,
        private_token=SecretStr("glpat-not-a-real-token-000000"),
        max_job_trace_bytes=16,
    )
    small_cap_client = GitLabClient(settings, common=common)
    big_body = b"y" * 1024
    readonly_respx_router.get(f"{API}/projects/42/jobs/5001/trace").mock(
        return_value=httpx.Response(200, content=big_body, headers={"Content-Length": "4"})
    )

    with pytest.raises(ToolError) as exc:
        await small_cap_client.get_job_trace("42", "5001")

    assert exc.value.code == ErrorCode.RESPONSE_TOO_LARGE
