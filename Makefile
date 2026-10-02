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
	uv run pytest --cov=mcp_common --cov=mcp_confluence --cov=mcp_gitlab --cov=mcp_opensearch \
		--cov=mcp_kibana --cov=mcp_cloudwatch --cov=mcp_kafka --cov=mcp_redis \
		--cov=mcp_sqs_sns --cov=mcp_pgvector --cov=mcp_ingest --cov=mcp_jira --cov=mcp_knowledge \
		--cov-report=term-missing

validate-contract:
	uv run python scripts/validate_contract.py "$$(uv run python -c 'from mcp_common.contract_testing import find_contract_path; print(find_contract_path())')"

readonly:
	uv run pytest -k "readonly" -m "not live"

# make ci runs the 5 local verification steps (T-003):
# lint -> typecheck -> unit test+coverage -> contract validation -> readonly suite of every package
ci: lint typecheck coverage validate-contract readonly
