"""T-055 / spike S5: `source_id` stability and `visibility` derivation rules per connector
(docs/spikes/S5-visibility-source-id.md). Team-only corpus (ADR-0016 Part 2, user decision
2026-10-01): a document is labelled `team` only when it can be *proved* team-visible; everything
else is labelled `restricted` so the ingest stage (T-073) can reject it.
"""

from __future__ import annotations

import pytest
from mcp_ingest.identity import (
    confluence_source_id,
    confluence_visibility,
    gitlab_source_id,
    gitlab_visibility,
    opensearch_alias,
    opensearch_source_id,
    opensearch_visibility,
)

# -- source_id ---------------------------------------------------------------------------------


def test_confluence_source_id_is_the_page_id_not_title_or_space() -> None:
    assert confluence_source_id(123456) == "123456"
    assert confluence_source_id("123456") == "123456"


def test_confluence_source_id_survives_rename_and_space_move() -> None:
    # same page id before and after a rename / move => same row (UNIQUE source_type, source_id)
    assert confluence_source_id("77") == confluence_source_id(77)


def test_confluence_source_id_rejects_a_non_numeric_id() -> None:
    with pytest.raises(ValueError):
        confluence_source_id("Payment retry policy")


def test_gitlab_source_ids_use_the_numeric_project_id_so_renames_do_not_fork_documents() -> None:
    assert gitlab_source_id(42, "blob", "src/app/main.py") == "42:blob:src/app/main.py"
    assert gitlab_source_id(42, "mr", 7) == "42:mr:7"
    assert gitlab_source_id(42, "issue", 19) == "42:issue:19"


def test_gitlab_source_id_rejects_unknown_kind_and_empty_ref() -> None:
    with pytest.raises(ValueError):
        gitlab_source_id(42, "wiki", "x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        gitlab_source_id(42, "blob", "")


@pytest.mark.parametrize(
    ("index", "alias"),
    [
        ("logs-app-000007", "logs-app"),  # ILM rollover suffix
        ("logs-app-000008", "logs-app"),
        ("app-logs-2026.09.30", "app-logs"),  # daily index
        ("app-logs-2026-09-30", "app-logs"),
        ("app-logs-2026.09", "app-logs"),  # monthly index
        (".ds-app-logs-2026.09.30-000012", "app-logs"),  # data-stream backing index
        ("postmortems", "postmortems"),  # not rolled over at all
        ("postmortems-v2", "postmortems-v2"),  # a version suffix is NOT a rollover suffix
        ("runbooks-2026-q3", "runbooks-2026-q3"),
    ],
)
def test_S5_opensearch_alias_strips_only_rollover_suffixes(index: str, alias: str) -> None:
    assert opensearch_alias(index) == alias


def test_S5_opensearch_source_id_is_stable_across_ilm_rollover() -> None:
    before = opensearch_source_id("postmortems-000001", "doc-abc")
    after = opensearch_source_id("postmortems-000002", "doc-abc")
    assert before == after == "postmortems:doc-abc"
    assert opensearch_source_id("postmortems-000002", "doc-xyz") != after


def test_opensearch_source_id_rejects_an_empty_document_id() -> None:
    with pytest.raises(ValueError):
        opensearch_source_id("postmortems-000001", "")


# -- Confluence visibility ---------------------------------------------------------------------

TEAM = frozenset({"PAY", "OPS"})


def conf(**overrides):
    base = {
        "space_key": "PAY", "space_type": "global", "team_spaces": TEAM,
        "page_read_restricted": False, "ancestors_read_restricted": False,
    }  # fmt: skip
    return confluence_visibility(**{**base, **overrides})


def test_S5_confluence_team_space_open_page_is_team() -> None:
    assert conf() == "team"


@pytest.mark.parametrize(
    "overrides",
    [
        {"space_key": "HR"},  # not declared team-wide by the operator
        {"space_type": "personal"},  # personal spaces are never team content
        {"page_read_restricted": True},
        {"ancestors_read_restricted": True},  # restrictions are inherited from ancestors
        {"team_spaces": frozenset()},  # nothing declared => nothing provably team
        {"page_read_restricted": None},  # unknown => cannot prove => restricted
        {"ancestors_read_restricted": None},
        {"space_type": None},
    ],
)
def test_S5_confluence_anything_unproven_is_restricted(overrides: dict) -> None:
    assert conf(**overrides) == "restricted"


def test_S5_confluence_space_key_match_is_case_insensitive() -> None:
    assert conf(space_key="pay") == "team"


# -- GitLab visibility -------------------------------------------------------------------------


def gl(**overrides):
    base = {
        "kind": "blob", "project_visibility": "internal", "feature_access": "enabled",
        "confidential": False, "project_path": "payments/worker",
        "team_projects": frozenset(), "crawler_access_level": 30,
    }  # fmt: skip
    return gitlab_visibility(**{**base, **overrides})


@pytest.mark.parametrize("visibility", ["public", "internal"])
def test_S5_gitlab_public_and_internal_projects_are_team(visibility: str) -> None:
    assert gl(project_visibility=visibility) == "team"


def test_S5_gitlab_internal_can_be_declared_not_team_for_instances_with_outsiders() -> None:
    assert gl(project_visibility="internal", internal_is_team=False) == "restricted"
    assert gl(project_visibility="public", internal_is_team=False) == "team"


def test_S5_gitlab_private_project_is_restricted_unless_declared_team_and_readable() -> None:
    assert gl(project_visibility="private") == "restricted"
    declared = frozenset({"payments"})
    assert gl(project_visibility="private", team_projects=declared) == "team"
    assert gl(project_visibility="private", team_projects=frozenset({"payments/worker"})) == "team"
    # Guests cannot read repository code in a private project: the crawler role matters.
    assert (
        gl(project_visibility="private", team_projects=declared, crawler_access_level=10)
        == "restricted"
    )
    assert (
        gl(project_visibility="private", team_projects=declared, crawler_access_level=None)
        == "restricted"
    )


def test_S5_gitlab_team_prefix_match_is_on_path_segments_not_raw_prefix() -> None:
    declared = frozenset({"pay"})
    assert gl(project_visibility="private", team_projects=declared, project_path="pay/x") == "team"
    assert (
        gl(project_visibility="private", team_projects=declared, project_path="payroll/x")
        == "restricted"
    )


@pytest.mark.parametrize("kind", ["issue", "mr"])
def test_S5_gitlab_confidential_items_are_restricted(kind: str) -> None:
    assert gl(kind=kind, confidential=True) == "restricted"
    assert gl(kind=kind, confidential=False) == "team"


@pytest.mark.parametrize("access", ["private", "disabled", None])
def test_S5_gitlab_feature_limited_to_members_is_restricted(access: str | None) -> None:
    assert gl(feature_access=access) == "restricted"


def test_S5_gitlab_unknown_project_visibility_is_restricted() -> None:
    assert gl(project_visibility=None) == "restricted"
    assert gl(project_visibility="weird") == "restricted"


# -- OpenSearch visibility ---------------------------------------------------------------------


def test_S5_opensearch_team_only_for_allowlisted_alias_and_rollover_index_inherits() -> None:
    allow = frozenset({"postmortems"})
    assert opensearch_visibility("postmortems-000003", allowlist=allow) == "team"
    assert opensearch_visibility("postmortems", allowlist=allow) == "team"
    assert opensearch_visibility("logs-app-000001", allowlist=allow) == "restricted"
    assert opensearch_visibility("postmortems", allowlist=frozenset()) == "restricted"
