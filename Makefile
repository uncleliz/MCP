.PHONY: sync lint typecheck test coverage validate-contract readonly ci

sync:
	uv sync --all-packages

lint:
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

coverage:
	uv run pytest --cov=mcp_common --cov-report=term-missing

validate-contract:
	uv run python scripts/validate_contract.py docs/squad/mcp-data-platform/api-contract.yaml

readonly:
	uv run pytest -k "readonly" -m "not live"

# make ci runs the 5 local verification steps (T-003):
# lint -> typecheck -> unit test+coverage -> contract validation -> readonly suite of every package
ci: lint typecheck coverage validate-contract readonly
