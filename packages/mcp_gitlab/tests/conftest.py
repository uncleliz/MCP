"""Shared fixtures for mcp-gitlab tests.

JSON fixtures under tests/fixtures/gitlab/ are hand-written to the shape of the GitLab REST
API v4 documentation (no live GitLab is reachable from this container); they have NOT been
captured from a real instance. `test_integration.py` (marker `live`) covers the real thing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import respx
from mcp_common.config import CommonSettings
from mcp_common.testing import readonly_respx_router  # noqa: F401  (re-exported fixture)
from mcp_gitlab.client import GitLabClient
from mcp_gitlab.read_api import GitLabReadApi
from mcp_gitlab.settings import Settings
from pydantic import SecretStr

# tests/ has no __init__.py (module names would collide across packages), so make the
# sibling helper module importable explicitly.
sys.path.insert(0, str(Path(__file__).parent))

from gitlab_helpers import API, BASE, PROJ, json_response, load_fixture  # noqa: E402


@pytest.fixture
def fixture() -> Callable[[str], Any]:
    return load_fixture


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url=BASE, private_token=SecretStr("glpat-not-a-real-token-000000"))


@pytest.fixture
def common() -> CommonSettings:
    return CommonSettings(http_backoff_base=0.0)


@pytest.fixture
def client(settings: Settings, common: CommonSettings) -> GitLabClient:
    return GitLabClient(settings, common=common)


@pytest.fixture
def read_api(client: GitLabClient, common: CommonSettings) -> GitLabReadApi:
    return GitLabReadApi(client, common)


@pytest.fixture
def gitlab(readonly_respx_router: respx.MockRouter) -> respx.MockRouter:  # noqa: F811
    """Router with the project lookups most tools perform first."""
    readonly_respx_router.get(f"{API}/projects/{PROJ}").mock(
        return_value=json_response("project_42.json")
    )
    readonly_respx_router.get(f"{API}/projects/42").mock(
        return_value=json_response("project_42.json")
    )
    readonly_respx_router.get(f"{API}/projects/43").mock(
        return_value=json_response("project_43.json")
    )
    return readonly_respx_router
