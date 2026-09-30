"""T-005: mcp_common.config.

Covers: CommonSettings defaults/env override, SourceSettingsBase `*_FILE` support,
fail-fast on missing required field naming the exact env var, and SecretStr never
leaking in repr/str.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_common.config import (
    CommonSettings,
    SourceMisconfiguredError,
    SourceSettingsBase,
    load_settings,
)
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict


class _ExampleSourceSettings(SourceSettingsBase):
    """A stand-in per-source Settings, shaped like mcp_confluence's future one."""

    model_config = SettingsConfigDict(env_prefix="MCP_EXAMPLE_", case_sensitive=False)

    base_url: str
    api_token: SecretStr


def test_common_settings_defaults_match_adr_0006_a2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HTTP_CONNECT_TIMEOUT", raising=False)
    settings = CommonSettings()

    assert settings.http_connect_timeout == 3.0
    assert settings.http_read_timeout == 7.0
    assert settings.http_max_retries == 1
    assert settings.tool_deadline == 25.0
    assert settings.transport == "stdio"
    assert settings.allow_unverified_credentials is False


def test_common_settings_reads_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOOL_DEADLINE", "60")
    monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")

    settings = CommonSettings()

    assert settings.tool_deadline == 60.0
    assert settings.log_level == "DEBUG"


def test_missing_required_field_raises_source_misconfigured_with_exact_var_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_EXAMPLE_BASE_URL", raising=False)
    monkeypatch.delenv("MCP_EXAMPLE_API_TOKEN", raising=False)

    with pytest.raises(SourceMisconfiguredError) as exc_info:
        load_settings(_ExampleSourceSettings, source="example")

    assert "MCP_EXAMPLE_BASE_URL" in exc_info.value.missing_vars
    assert "MCP_EXAMPLE_API_TOKEN" in exc_info.value.missing_vars
    assert "MCP_EXAMPLE_BASE_URL" in str(exc_info.value)


def test_partial_missing_field_only_names_that_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EXAMPLE_BASE_URL", "https://example.test")
    monkeypatch.delenv("MCP_EXAMPLE_API_TOKEN", raising=False)

    with pytest.raises(SourceMisconfiguredError) as exc_info:
        load_settings(_ExampleSourceSettings, source="example")

    assert exc_info.value.missing_vars == ["MCP_EXAMPLE_API_TOKEN"]


def test_file_suffix_env_var_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("super-secret-token\n", encoding="utf-8")
    monkeypatch.setenv("MCP_EXAMPLE_BASE_URL", "https://example.test")
    monkeypatch.delenv("MCP_EXAMPLE_API_TOKEN", raising=False)
    monkeypatch.setenv("MCP_EXAMPLE_API_TOKEN_FILE", str(token_file))

    settings = load_settings(_ExampleSourceSettings, source="example")

    assert settings.api_token.get_secret_value() == "super-secret-token"


def test_plain_env_var_wins_over_file_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("MCP_EXAMPLE_BASE_URL", "https://example.test")
    monkeypatch.setenv("MCP_EXAMPLE_API_TOKEN", "from-env")
    monkeypatch.setenv("MCP_EXAMPLE_API_TOKEN_FILE", str(token_file))

    settings = load_settings(_ExampleSourceSettings, source="example")

    assert settings.api_token.get_secret_value() == "from-env"


def test_secretstr_does_not_leak_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EXAMPLE_BASE_URL", "https://example.test")
    monkeypatch.setenv("MCP_EXAMPLE_API_TOKEN", "super-secret-token")

    settings = load_settings(_ExampleSourceSettings, source="example")

    assert "super-secret-token" not in repr(settings)
    assert "super-secret-token" not in str(settings)
    assert "super-secret-token" not in repr(settings.api_token)
