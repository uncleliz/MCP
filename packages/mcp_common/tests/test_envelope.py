"""T-008: mcp_common.envelope — ToolResult/Citation/Meta vs. api-contract.yaml."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp_common.envelope import Citation, Meta, ResultStatus, SourceType, ToolResult
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "docs" / "squad" / "mcp-data-platform" / "api-contract.yaml"


@pytest.fixture(scope="module")
def contract_spec() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _validate_against(spec: dict[str, Any], schema_name: str, instance: dict[str, Any]) -> None:
    schema = spec["components"]["schemas"][schema_name]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from jsonschema import Draft202012Validator, RefResolver

        resolver = RefResolver.from_schema(spec)
        Draft202012Validator(schema, resolver=resolver).validate(instance)


def _meta(**overrides: Any) -> dict[str, Any]:
    base = {
        "source": "confluence",
        "returned": 0,
        "has_more": False,
        "truncated": False,
        "elapsed_ms": 10,
        "as_of": "2026-10-01T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Construction-time invariants
# ---------------------------------------------------------------------------


def test_ok_with_empty_citations_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(status=ResultStatus.OK, items=[], citations=[], meta=Meta(**_meta()))


def test_partial_with_empty_citations_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(status=ResultStatus.PARTIAL, items=[], citations=[], meta=Meta(**_meta()))


def test_ok_with_citations_is_accepted() -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0, "title": "hello"}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="Page", uri="https://x")],
        meta=Meta(**_meta(returned=1)),
    )
    assert result.status == ResultStatus.OK


def test_empty_with_nonempty_items_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            status=ResultStatus.EMPTY,
            items=[{"citation_ref": 0}],
            citations=[],
            meta=Meta(**_meta()),
        )


def test_empty_with_nonempty_citations_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            status=ResultStatus.EMPTY,
            items=[],
            citations=[Citation(source_type=SourceType.CONFLUENCE, label="x", uri="https://x")],
            meta=Meta(**_meta()),
        )


def test_not_found_with_empty_items_and_citations_is_accepted() -> None:
    result = ToolResult(
        status=ResultStatus.NOT_FOUND, items=[], citations=[], meta=Meta(**_meta())
    )
    assert result.status == ResultStatus.NOT_FOUND


def test_has_more_without_next_cursor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Meta(**_meta(has_more=True, next_cursor=None))


def test_has_more_with_next_cursor_is_accepted() -> None:
    meta = Meta(**_meta(has_more=True, next_cursor="abc123"))
    assert meta.next_cursor == "abc123"


def test_citation_uri_none_is_allowed_for_sourceless_types() -> None:
    citation = Citation(
        source_type=SourceType.KAFKA, label="topic-x", uri=None, locator={"topic": "x"}
    )
    assert citation.uri is None


def test_forbids_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            status=ResultStatus.NOT_FOUND,
            items=[],
            citations=[],
            meta=Meta(**_meta()),
            extra_field="not allowed",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Contract conformance — all 4 status branches validate against ToolResultBase
# ---------------------------------------------------------------------------


def test_ok_branch_matches_contract_schema(contract_spec: dict[str, Any]) -> None:
    result = ToolResult(
        status=ResultStatus.OK,
        items=[{"citation_ref": 0}],
        citations=[Citation(source_type=SourceType.CONFLUENCE, label="Page", uri="https://x")],
        meta=Meta(**_meta(returned=1)),
    )
    _validate_against(
        contract_spec, "ToolResultBase", result.model_dump(mode="json", exclude_none=False)
    )


def test_empty_branch_matches_contract_schema(contract_spec: dict[str, Any]) -> None:
    result = ToolResult(status=ResultStatus.EMPTY, items=[], citations=[], meta=Meta(**_meta()))
    _validate_against(contract_spec, "ToolResultBase", result.model_dump(mode="json"))


def test_not_found_branch_matches_contract_schema(contract_spec: dict[str, Any]) -> None:
    result = ToolResult(status=ResultStatus.NOT_FOUND, items=[], citations=[], meta=Meta(**_meta()))
    _validate_against(contract_spec, "ToolResultBase", result.model_dump(mode="json"))


def test_partial_branch_matches_contract_schema(contract_spec: dict[str, Any]) -> None:
    result = ToolResult(
        status=ResultStatus.PARTIAL,
        items=[],
        citations=[
            Citation(
                source_type=SourceType.CLOUDWATCH,
                label="log group",
                locator={"log_group": "x"},
            )
        ],
        meta=Meta(**_meta(truncated=True)),
    )
    _validate_against(contract_spec, "ToolResultBase", result.model_dump(mode="json"))


def test_timestamp_is_serialized_as_iso8601_with_timezone() -> None:
    result = ToolResult(
        status=ResultStatus.NOT_FOUND,
        items=[],
        citations=[],
        meta=Meta(**_meta(as_of=datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC))),
    )
    dumped = result.model_dump(mode="json")
    assert dumped["meta"]["as_of"].endswith("Z") or "+00:00" in dumped["meta"]["as_of"]
