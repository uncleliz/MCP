"""T-003: `scripts/validate_contract.py` must catch a schema error deliberately
inserted into a copy of the real contract (Done-when of T-003 in implementation-plan.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from mcp_common.contract_testing import find_contract_path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Resolve via the central helper so the squad layout migration does not require patching
# every test file (R-fix: CI path).
CONTRACT_PATH = find_contract_path()
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_contract.py"


def _load_validate_contract_module():
    spec = importlib.util.spec_from_file_location("validate_contract", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_contract"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validate_contract_module():
    return _load_validate_contract_module()


def test_real_contract_is_valid(validate_contract_module) -> None:
    problems = validate_contract_module.validate_contract(CONTRACT_PATH)
    assert problems == []


def test_detects_deliberately_broken_copy_missing_required_openapi_field(
    validate_contract_module, tmp_path: Path
) -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    # Deliberately corrupt the document at the OpenAPI meta-schema level: `info.version`
    # is a required field of the OpenAPI 3.1 Info Object.
    del spec["info"]["version"]
    broken = tmp_path / "broken-contract.yaml"
    broken.write_text(yaml.safe_dump(spec), encoding="utf-8")

    problems = validate_contract_module.validate_contract(broken)

    assert problems != []
    assert any("structural validation failed" in p for p in problems)


def test_detects_operation_missing_x_readonly(validate_contract_module, tmp_path: Path) -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    first_path = next(iter(spec["paths"]))
    del spec["paths"][first_path]["post"]["x-readonly"]
    broken = tmp_path / "broken-readonly.yaml"
    broken.write_text(yaml.safe_dump(spec), encoding="utf-8")

    problems = validate_contract_module.validate_contract(broken)

    assert any("x-readonly" in p for p in problems)


def test_detects_error_code_result_status_overlap(validate_contract_module, tmp_path: Path) -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    spec["components"]["schemas"]["ErrorCode"]["enum"].append("ok")
    broken = tmp_path / "broken-overlap.yaml"
    broken.write_text(yaml.safe_dump(spec), encoding="utf-8")

    problems = validate_contract_module.validate_contract(broken)

    assert any("overlap" in p for p in problems)


def test_main_returns_nonzero_for_broken_contract(
    validate_contract_module, tmp_path: Path, capsys
) -> None:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    del spec["info"]["version"]
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(spec), encoding="utf-8")

    exit_code = validate_contract_module.main(["validate_contract.py", str(broken)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "CONTRACT INVALID" in captured.err


def test_main_returns_zero_for_real_contract(capsys) -> None:
    module = _load_validate_contract_module()
    exit_code = module.main(["validate_contract.py", str(CONTRACT_PATH)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out
