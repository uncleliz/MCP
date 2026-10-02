"""T-093: the Jira identity rules added for the connector (`jira_source_id`, `jira_visibility`).

Default-deny visibility (ADR-0016 A2 / ADR-0019): only a declared team project with no issue
security level and a not-restricted project is `team`.
"""

from __future__ import annotations

import pytest
from mcp_ingest.identity import jira_source_id, jira_visibility

TEAM = ["PAY", "OPS"]


def test_jira_source_id_normalises_and_validates() -> None:
    assert jira_source_id("pay-1234") == "PAY-1234"
    assert jira_source_id(" CORE-7 ") == "CORE-7"
    for bad in ("PAY", "1234", "PAY_1", "p ay-1", ""):
        with pytest.raises(ValueError, match="issue key"):
            jira_source_id(bad)


@pytest.mark.parametrize(
    ("project", "security", "restricted", "expected"),
    [
        ("PAY", None, False, "team"),           # declared, no security, not restricted
        ("pay", None, False, "team"),           # case-insensitive match
        ("HR", None, False, "restricted"),      # not declared team-wide
        ("PAY", "Developers", False, "restricted"),  # issue-level security level
        ("PAY", None, True, "restricted"),      # project restricted
        ("PAY", None, None, "restricted"),      # restriction state unknown => default-deny
    ],
)
def test_jira_visibility_default_deny(project, security, restricted, expected) -> None:
    assert (
        jira_visibility(
            project_key=project,
            team_projects=TEAM,
            issue_security_level=security,
            project_restricted=restricted,
        )
        == expected
    )
