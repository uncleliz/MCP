"""T-016: `mcp-common` management CLI — `config-emit` (NFR-005 verification).

This is a standalone terminal tool a human runs once per server to get a
`claude_desktop_config.json` snippet — it is never an MCP stdio server itself, so
(unlike every `mcp_<source>` package) writing to stdout here is exactly the point,
not a violation of the R2 stdout guard. Output goes through `sys.stdout.write()`
rather than `print()` purely so the workspace's global "no print()" lint rule (aimed
at actual MCP servers) does not need a file-specific exception.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["build_config_emit_payload", "main"]

# One entry per MCP server (architecture.md tool surface table + .env.example, T-004).
# `env_vars` lists the variables *this* server reads; secrets are emitted as an
# obvious placeholder the operator must fill in, never a real value.
_SERVER_REGISTRY: dict[str, dict[str, Any]] = {
    "confluence": {
        "package": "mcp-confluence",
        "env_vars": [
            "MCP_CONFLUENCE_BASE_URL",
            "MCP_CONFLUENCE_FLAVOR",
            "MCP_CONFLUENCE_EMAIL",
            "MCP_CONFLUENCE_API_TOKEN",
        ],
    },
    "gitlab": {
        "package": "mcp-gitlab",
        "env_vars": ["MCP_GITLAB_BASE_URL", "MCP_GITLAB_PRIVATE_TOKEN"],
    },
    "opensearch": {
        "package": "mcp-opensearch",
        "env_vars": ["MCP_OPENSEARCH_HOSTS", "MCP_OPENSEARCH_USERNAME", "MCP_OPENSEARCH_PASSWORD"],
    },
    "kibana": {
        "package": "mcp-kibana",
        "env_vars": ["MCP_KIBANA_BASE_URL", "MCP_KIBANA_USERNAME", "MCP_KIBANA_PASSWORD"],
    },
    "cloudwatch": {
        "package": "mcp-cloudwatch",
        "env_vars": [
            "MCP_CLOUDWATCH_REGION",
            "MCP_CLOUDWATCH_AWS_ACCESS_KEY_ID",
            "MCP_CLOUDWATCH_AWS_SECRET_ACCESS_KEY",
        ],
    },
    "kafka": {
        "package": "mcp-kafka",
        "env_vars": [
            "MCP_KAFKA_BOOTSTRAP_SERVERS",
            "MCP_KAFKA_SECURITY_PROTOCOL",
            "MCP_KAFKA_SASL_USERNAME",
            "MCP_KAFKA_SASL_PASSWORD",
        ],
    },
    "redis": {
        "package": "mcp-redis",
        "env_vars": ["MCP_REDIS_URL", "MCP_REDIS_PASSWORD"],
    },
    "sqs-sns": {
        "package": "mcp-sqs-sns",
        "env_vars": [
            "MCP_SQS_SNS_REGION",
            "MCP_SQS_SNS_AWS_ACCESS_KEY_ID",
            "MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY",
        ],
    },
    "pgvector": {
        "package": "mcp-pgvector",
        "env_vars": ["MCP_PGVECTOR_DSN", "MCP_PGVECTOR_EMBEDDING_MODEL"],
    },
}

_PLACEHOLDER = "<dien-gia-tri-that-vao-day>"


def known_servers() -> list[str]:
    return sorted(_SERVER_REGISTRY)


def build_config_emit_payload(server: str) -> dict[str, Any]:
    """Build the `claude_desktop_config.json` `mcpServers.<server>` snippet.

    Raises `ValueError` (with the list of known servers) for an unknown name.
    """
    entry = _SERVER_REGISTRY.get(server)
    if entry is None:
        raise ValueError(f"Unknown server '{server}'. Known servers: {known_servers()}")

    return {
        "mcpServers": {
            server: {
                "command": "uv",
                "args": ["run", "--package", entry["package"], entry["package"]],
                "env": {var: _PLACEHOLDER for var in entry["env_vars"]},
            }
        }
    }


def _config_emit(args: argparse.Namespace) -> int:
    try:
        payload = build_config_emit_payload(args.server)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-common")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_emit = subparsers.add_parser(
        "config-emit",
        help="Print a claude_desktop_config.json snippet for one MCP server.",
    )
    config_emit.add_argument("--server", required=True, choices=known_servers())
    config_emit.set_defaults(handler=_config_emit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
