#!/usr/bin/env python3
"""Manual eval runner for Phase 1 / Journey 1 (T-029, NFR-003).

For every question in `eval/questions.yaml` it calls the real Confluence and GitLab read
APIs (the same code the MCP tools run) and records, per source, the result status, how many
citations came back, and the citation URLs. Output is a markdown table written to
`eval/results/<UTC timestamp>.md` with empty columns for the human reviewer to fill in
(did Claude's final answer cite the sources / say a source was empty).

It measures what the *tools* return. Whether Claude's synthesised answer honours the
citation discipline is reviewed by hand in Claude Desktop/Code using the `dev_knowledge_lookup`
prompt (see docs/claude-usage/journey-1.md); that manual step is what NFR-003 counts.

    uv run python scripts/run_eval.py --dry-run          # validate questions only (no network)
    uv run python scripts/run_eval.py                    # live run (needs credentials + VPN)
    uv run python scripts/run_eval.py --only Q-J1-009    # one question

Read-only: it only ever calls the GET-only read APIs. Secrets are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "eval" / "questions.yaml"
DEFAULT_OUT = ROOT / "eval" / "results"
SOURCES = ("confluence", "gitlab")
REQUIRED_FIELDS = (
    "id", "question", "covers", "confluence_query", "gitlab_query",
    "expected_sources", "expect_empty", "expected_behavior",
)  # fmt: skip


@dataclass
class SourceResult:
    status: str  # ok | partial | empty | not_found | error:<code>
    citations: int = 0
    urls: list[str] = field(default_factory=list)
    first_line: str = ""


@dataclass
class Row:
    question: dict[str, Any]
    results: dict[str, SourceResult]
    verdict: str = ""
    problems: list[str] = field(default_factory=list)


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["questions"])


def validate_questions(questions: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for index, question in enumerate(questions):
        label = question.get("id", f"#{index}")
        for name in REQUIRED_FIELDS:
            if name not in question:
                problems.append(f"{label}: missing field '{name}'")
        if label in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(label)
        for name in ("expected_sources", "expect_empty"):
            for source in question.get(name, []):
                if source not in SOURCES:
                    problems.append(f"{label}: unknown source '{source}' in {name}")
        if len(str(question.get("gitlab_query", ""))) < 2:
            problems.append(f"{label}: gitlab_query must be at least 2 characters")
    if len(questions) < 10:
        problems.append(f"need at least 10 questions, found {len(questions)}")
    if not any(q.get("expect_empty") for q in questions):
        problems.append("need at least one case where a source is expected to be empty")
    return problems


async def _run_source(call: Any) -> SourceResult:
    from mcp_common.errors import ToolError
    from mcp_common.render import render_text

    try:
        outcome = await call()
    except ToolError as exc:
        return SourceResult(status=f"error:{exc.code.value}")
    result = outcome.result
    text = render_text(
        result, query_description=outcome.query_description, identifier=outcome.identifier
    )
    return SourceResult(
        status=result.status.value,
        citations=len(result.citations),
        urls=[c.uri for c in result.citations if c.uri],
        first_line=text.splitlines()[0] if text else "",
    )


def judge(question: dict[str, Any], results: dict[str, SourceResult]) -> tuple[str, list[str]]:
    """Tool-level verdict against the question's expectations (not Claude's answer)."""
    problems: list[str] = []
    for source in question["expect_empty"]:
        got = results[source]
        if got.status != "empty":
            problems.append(f"{source}: expected status=empty, got {got.status}")
        elif not got.first_line.startswith("Không tìm thấy"):
            problems.append(f"{source}: empty result does not render 'Không tìm thấy ...'")
    for source in question["expected_sources"]:
        got = results[source]
        if got.status not in ("ok", "partial") or got.citations < 1:
            problems.append(f"{source}: expected >=1 citation, got {got.status}/{got.citations}")
    return ("PASS" if not problems else "FAIL"), problems


async def evaluate(question: dict[str, Any], confluence_api: Any, gitlab_api: Any) -> Row:
    confluence, gitlab = await asyncio.gather(
        _run_source(
            lambda: confluence_api.search_pages(query=question["confluence_query"], limit=5)
        ),
        _run_source(lambda: gitlab_api.search_code(query=question["gitlab_query"], limit=5)),
    )
    results = {"confluence": confluence, "gitlab": gitlab}
    verdict, problems = judge(question, results)
    return Row(question, results, verdict, problems)


def _cell(result: SourceResult) -> str:
    return f"{result.status} ({result.citations} cit.)"


def render_table(rows: list[Row], *, started: datetime) -> str:
    cited = sum(1 for row in rows if any(r.citations for r in row.results.values()))
    lines = [
        "# Kết quả eval Phase 1 — Journey 1",
        "",
        f"- Thời điểm chạy: {started.isoformat(timespec='seconds')}",
        f"- Số câu hỏi: {len(rows)}; câu có ít nhất một citation từ tool: {cited}/{len(rows)}",
        "- Cột *Claude trả lời có citation?* và *Nêu rõ nguồn rỗng?* do người review điền tay "
        "sau khi hỏi Claude bằng prompt `dev_knowledge_lookup` (NFR-003).",
        "",
        "| ID | Confluence | GitLab | Kỳ vọng tool | Claude trả lời có citation? | "
        "Nêu rõ nguồn rỗng? | Ghi chú |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        note = "; ".join(row.problems) or ""
        lines.append(
            f"| {row.question['id']} | {_cell(row.results['confluence'])} | "
            f"{_cell(row.results['gitlab'])} | {row.verdict} | ☐ | "
            f"{'☐' if row.question['expect_empty'] else 'n/a'} | {note} |"
        )
    lines += ["", "## Citation trả về theo câu hỏi", ""]
    for row in rows:
        lines.append(f"### {row.question['id']} — {row.question['question']}")
        for source in SOURCES:
            result = row.results[source]
            lines.append(f"- {source}: {result.status}")
            lines += [f"  - {url}" for url in result.urls]
            if result.status == "empty":
                lines.append(f"  - câu trả lời kỳ vọng: {result.first_line}")
        lines.append("")
    return "\n".join(lines)


def _build_apis() -> tuple[Any, Any] | None:
    from mcp_common.config import SourceMisconfiguredError
    from mcp_confluence.server import build_read_api as build_confluence
    from mcp_gitlab.server import build_read_api as build_gitlab

    try:
        return build_confluence(), build_gitlab()
    except SourceMisconfiguredError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return None


async def run(questions: list[dict[str, Any]], out_dir: Path) -> tuple[Path, list[Row]]:
    apis = _build_apis()
    if apis is None:
        raise SystemExit(2)
    started = datetime.now(UTC)
    rows = [await evaluate(question, *apis) for question in questions]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.md"
    path.write_text(render_table(rows, started=started), encoding="utf-8")
    return path, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", help="run a single question id")
    parser.add_argument("--dry-run", action="store_true", help="validate questions, no network")
    args = parser.parse_args(argv)

    questions = load_questions(args.questions)
    problems = validate_questions(questions)
    if problems:
        sys.stderr.write("questions file invalid:\n" + "\n".join(f"- {p}" for p in problems) + "\n")
        return 1
    if args.only:
        questions = [q for q in questions if q["id"] == args.only]
        if not questions:
            sys.stderr.write(f"no question with id {args.only}\n")
            return 1
    if args.dry_run:
        sys.stdout.write(f"OK: {len(questions)} questions valid (dry run, nothing executed)\n")
        return 0
    path, rows = asyncio.run(run(questions, args.out))
    failed = [row.question["id"] for row in rows if row.verdict == "FAIL"]
    sys.stdout.write(f"wrote {path}\n")
    if failed:
        sys.stdout.write(f"tool-level expectations not met: {', '.join(failed)}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
