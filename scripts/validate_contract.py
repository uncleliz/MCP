#!/usr/bin/env python3
"""Validate `api-contract.yaml` (ADR-0013).

No `spectral`/`redocly` OpenAPI linter is available on dev machines (ADR-0013), so
validation here is done with:

1. Structural OpenAPI 3.1 validation via `openapi-spec-validator`.
2. A couple of global invariants the contract's own `info.description` documents as
   machine-checkable (ADR-0004 / ADR-0013 / ADR-0003 A2):
   - every operation is read-only (`x-readonly: true`, `x-side-effects: none`),
     except `x-interface: cli` operations (the `mcp-ingest` CLI, which writes to `kb`
     and documents that explicitly instead).
   - the `ErrorCode` and `ResultStatus` enums are disjoint (an error code must never
     also be a valid success status, and vice versa).

Exit code 0 = valid. Exit code 1 = invalid, with one `CONTRACT INVALID: ...` line per
problem printed to stderr. Exit code 2 = usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _check_readonly_invariant(spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId", f"{method.upper()} {path}")
            if operation.get("x-interface") == "cli":
                continue
            if operation.get("x-readonly") is not True:
                problems.append(f"{op_id}: missing `x-readonly: true`")
            if operation.get("x-side-effects") != "none":
                problems.append(f"{op_id}: missing `x-side-effects: none`")
    return problems


def _check_error_code_disjoint_from_result_status(spec: dict[str, Any]) -> list[str]:
    schemas = spec.get("components", {}).get("schemas", {})
    error_codes = set(schemas.get("ErrorCode", {}).get("enum", []) or [])
    result_statuses = set(schemas.get("ResultStatus", {}).get("enum", []) or [])
    overlap = error_codes & result_statuses
    if overlap:
        return [f"ErrorCode and ResultStatus enums overlap: {sorted(overlap)}"]
    return []


def validate_contract(path: Path) -> list[str]:
    """Return a list of human-readable problems; empty list == valid contract."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        return [f"{path}: does not parse to a YAML mapping"]

    problems: list[str] = []
    try:
        validate_openapi(spec)
    except OpenAPIValidationError as exc:
        problems.append(f"OpenAPI structural validation failed: {exc}")

    problems.extend(_check_readonly_invariant(spec))
    problems.extend(_check_error_code_disjoint_from_result_status(spec))
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_contract.py <api-contract.yaml>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    problems = validate_contract(path)
    if problems:
        for problem in problems:
            print(f"CONTRACT INVALID: {problem}", file=sys.stderr)
        return 1

    print(f"OK: {path} is a valid contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
