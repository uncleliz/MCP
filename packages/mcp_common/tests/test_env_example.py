"""T-004: `.env.example` must list every shared variable architecture.md's "Config"
section names, and must never contain a real-looking secret.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

REQUIRED_SHARED_VARS = [
    "MCP_HTTP_CONNECT_TIMEOUT",
    "MCP_HTTP_READ_TIMEOUT",
    "MCP_HTTP_WRITE_TIMEOUT",
    "MCP_HTTP_POOL_TIMEOUT",
    "MCP_HTTP_MAX_RETRIES",
    "MCP_HTTP_BACKOFF_BASE",
    "MCP_TOOL_DEADLINE",
    "MCP_MAX_OUTPUT_BYTES",
    "MCP_MAX_TIME_RANGE_DAYS",
    "MCP_LOG_LEVEL",
    "MCP_REDACT_DISABLED",
    "MCP_TRANSPORT",
    "MCP_ALLOW_UNVERIFIED_CREDENTIALS",
]

# One representative variable per source, to prove every MCP_<SOURCE>_ prefix is documented.
REQUIRED_SOURCE_PREFIXES = [
    "MCP_CONFLUENCE_",
    "MCP_GITLAB_",
    "MCP_OPENSEARCH_",
    "MCP_KIBANA_",
    "MCP_CLOUDWATCH_",
    "MCP_KAFKA_",
    "MCP_REDIS_",
    "MCP_SQS_SNS_",
    "MCP_PGVECTOR_",
    "MCP_INGEST_",
]

# Heuristics for real-looking secrets accidentally committed (AWS access key id shape,
# a private key block, a JWT). ".env.example must never contain a real secret."
_SUSPICIOUS_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


def test_env_example_exists() -> None:
    assert ENV_EXAMPLE.is_file()


def test_env_example_lists_every_shared_variable() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [var for var in REQUIRED_SHARED_VARS if var not in content]
    assert missing == []


def test_env_example_documents_every_source_prefix() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = [prefix for prefix in REQUIRED_SOURCE_PREFIXES if prefix not in content]
    assert missing == []


def test_env_example_has_no_real_secret() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    hits = [pattern.pattern for pattern in _SUSPICIOUS_PATTERNS if pattern.search(content)]
    assert hits == []


def test_repo_has_no_committed_dotenv_file() -> None:
    # Only `.env.example` may exist; a real `.env` must never be committed.
    assert not (REPO_ROOT / ".env").exists()


# -- CHG-003 (T-121, FR-025 / FR-024): Confluence Cloud real-ingest placeholders ---------------


def test_env_example_documents_egress_allowlist_default_deny() -> None:
    # The egress allow-list must be present and default to EMPTY (default-deny, ADR-0023 §6a).
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "MCP_EGRESS_ALLOWLIST" in content
    # The configured default is empty (deny-all): the bare assignment with no value.
    assert re.search(r"(?m)^MCP_EGRESS_ALLOWLIST=\s*$", content)
    # The example comment shows the atlassian + huggingface pattern.
    assert "*.atlassian.net" in content


def test_env_example_confluence_token_is_file_based_and_clearly_fake() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    # The *_FILE convention is documented for the read-only token (preferred, never committed).
    assert "MCP_CONFLUENCE_API_TOKEN_FILE" in content
    # The read-only + never-committed guidance is spelled out.
    lowered = content.lower()
    assert "read-only" in lowered
    assert "never committed" in lowered
    # The inline token value is an obvious placeholder, not a real-looking credential.
    match = re.search(r"(?m)^MCP_CONFLUENCE_API_TOKEN=(\S+)\s*$", content)
    assert match is not None
    assert match.group(1).startswith("changeme")
