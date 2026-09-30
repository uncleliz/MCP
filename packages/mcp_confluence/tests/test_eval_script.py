"""T-029: eval question set + manual eval runner (scripts/run_eval.py).

AC: FR-003/AC-001, FR-003/AC-002, FR-015/AC-001, FR-015/AC-002, NFR-003.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from mcp_common.envelope import Citation, SourceType
from mcp_common.errors import ErrorCode, ToolError
from mcp_common.tooling import ToolOutcome, build_result

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run_eval.py"


@pytest.fixture(scope="module")
def run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = module
    spec.loader.exec_module(module)
    return module


def test_FR_003_questions_file_is_valid_and_large_enough(run_eval) -> None:
    questions = run_eval.load_questions(ROOT / "eval" / "questions.yaml")
    assert len(questions) >= 10
    assert run_eval.validate_questions(questions) == []


def test_FR_003_AC_002_questions_include_empty_source_cases(run_eval) -> None:
    questions = run_eval.load_questions(ROOT / "eval" / "questions.yaml")
    empty_confluence = [q for q in questions if q["expect_empty"] == ["confluence"]]
    empty_gitlab = [q for q in questions if q["expect_empty"] == ["gitlab"]]
    both = [q for q in questions if sorted(q["expect_empty"]) == ["confluence", "gitlab"]]
    assert empty_confluence and empty_gitlab and both
    # the empty-Confluence case must still expect a GitLab citation (and only that)
    assert empty_confluence[0]["expected_sources"] == ["gitlab"]


def test_validate_questions_reports_problems(run_eval) -> None:
    problems = run_eval.validate_questions(
        [{"id": "A", "question": "q", "expected_sources": ["jira"], "expect_empty": []}] * 2
    )
    text = " ".join(problems)
    assert "missing field" in text and "duplicate id" in text
    assert "unknown source 'jira'" in text and "at least 10" in text
    assert "expected to be empty" in text


class _FakeApi:
    def __init__(self, source: SourceType, hits: int, *, error: bool = False) -> None:
        self.source, self.hits, self.error = source, hits, error

    async def _outcome(self, description: str) -> ToolOutcome:
        if self.error:
            raise ToolError(ErrorCode.UPSTREAM_TIMEOUT, "boom", self.source.value, True)
        items = [{"citation_ref": i} for i in range(self.hits)]
        citations = [
            Citation(source_type=self.source, label=f"d{i}", uri=f"https://x.test/{i}")
            for i in range(self.hits)
        ]
        result = build_result(self.source, items, citations, started=0.0, query_echo={"q": 1})
        return ToolOutcome(result, query_description=description)

    async def search_pages(self, *, query: str, limit: int) -> ToolOutcome:
        return await self._outcome(f'tài liệu Confluence cho "{query}"')

    async def search_code(self, *, query: str, limit: int) -> ToolOutcome:
        return await self._outcome(f'code GitLab cho "{query}"')


def _question(run_eval, qid: str) -> dict:
    return next(
        q for q in run_eval.load_questions(ROOT / "eval" / "questions.yaml") if q["id"] == qid
    )


@pytest.mark.asyncio
async def test_FR_003_AC_001_both_sources_cited_passes(run_eval) -> None:
    row = await run_eval.evaluate(
        _question(run_eval, "Q-J1-001"),
        _FakeApi(SourceType.CONFLUENCE, 2),
        _FakeApi(SourceType.GITLAB, 3),
    )
    assert row.verdict == "PASS" and row.problems == []
    assert row.results["confluence"].citations == 2 and row.results["gitlab"].citations == 3


@pytest.mark.asyncio
async def test_FR_003_AC_002_empty_confluence_renders_not_found_sentence(run_eval) -> None:
    question = _question(run_eval, "Q-J1-009")
    row = await run_eval.evaluate(
        question, _FakeApi(SourceType.CONFLUENCE, 0), _FakeApi(SourceType.GITLAB, 1)
    )
    assert row.verdict == "PASS"
    first_line = row.results["confluence"].first_line
    assert first_line.startswith("Không tìm thấy tài liệu Confluence cho")
    assert "zzqx-khong-ton-tai-7781" in first_line


@pytest.mark.asyncio
async def test_unmet_expectations_and_errors_are_flagged(run_eval) -> None:
    question = _question(run_eval, "Q-J1-001")
    row = await run_eval.evaluate(
        question, _FakeApi(SourceType.CONFLUENCE, 0), _FakeApi(SourceType.GITLAB, 0, error=True)
    )
    assert row.verdict == "FAIL"
    assert row.results["gitlab"].status == "error:upstream_timeout"
    assert len(row.problems) == 2
    unexpected = await run_eval.evaluate(
        _question(run_eval, "Q-J1-009"),
        _FakeApi(SourceType.CONFLUENCE, 2),
        _FakeApi(SourceType.GITLAB, 1),
    )
    assert unexpected.verdict == "FAIL" and "expected status=empty" in unexpected.problems[0]


@pytest.mark.asyncio
async def test_NFR_003_table_lists_citations_and_manual_columns(run_eval) -> None:
    from datetime import UTC, datetime

    rows = [
        await run_eval.evaluate(
            _question(run_eval, qid),
            _FakeApi(SourceType.CONFLUENCE, hits),
            _FakeApi(SourceType.GITLAB, 1),
        )
        for qid, hits in (("Q-J1-001", 2), ("Q-J1-009", 0))
    ]
    table = run_eval.render_table(rows, started=datetime(2026, 10, 1, tzinfo=UTC))
    assert "| Q-J1-001 | ok (2 cit.) | ok (1 cit.) | PASS | ☐ | n/a |" in table
    assert "| Q-J1-009 | empty (0 cit.) | ok (1 cit.) | PASS | ☐ | ☐ |" in table
    assert "https://x.test/0" in table and "Không tìm thấy" in table
    assert "Claude trả lời có citation?" in table


def test_main_dry_run_and_bad_args(run_eval, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_eval.main(["--dry-run"]) == 0
    assert "12 questions valid" in capsys.readouterr().out
    assert run_eval.main(["--dry-run", "--only", "Q-J1-003"]) == 0
    assert run_eval.main(["--dry-run", "--only", "NOPE"]) == 1


def test_main_live_without_credentials_exits_2(
    run_eval, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for var in (
        "MCP_CONFLUENCE_BASE_URL", "MCP_CONFLUENCE_EMAIL", "MCP_CONFLUENCE_API_TOKEN",
        "MCP_GITLAB_BASE_URL", "MCP_GITLAB_PRIVATE_TOKEN",
    ):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        run_eval.main([])
    assert exc.value.code == 2
    assert "MCP_CONFLUENCE" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_writes_results_file(run_eval, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    questions = [q for q in run_eval.load_questions(ROOT / "eval" / "questions.yaml")][:2]
    monkeypatch.setattr(
        run_eval,
        "_build_apis",
        lambda: (_FakeApi(SourceType.CONFLUENCE, 1), _FakeApi(SourceType.GITLAB, 1)),
    )
    path, rows = await run_eval.run(questions, tmp_path / "results")
    assert path.exists() and path.suffix == ".md" and len(rows) == 2
    assert "Q-J1-001" in path.read_text(encoding="utf-8")
