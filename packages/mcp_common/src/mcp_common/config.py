"""T-005: shared configuration loading for every MCP Data Platform server.

Two building blocks:

* :class:`CommonSettings` — the shared `MCP_*` variables every server reads
  (HTTP timeout budget, tool deadline, output/time-range caps, logging, transport,
  the credential-gate escape hatch). See architecture.md "Config".
* :class:`SourceSettingsBase` — the base class every per-source `Settings` (built in
  Phase 1/2/3, e.g. `mcp_confluence.settings.Settings`) subclasses. It adds:
  - `*_FILE` support for any field: `MCP_<SOURCE>_API_TOKEN_FILE=/path` is read and
    used if the plain `MCP_<SOURCE>_API_TOKEN` env var is not set (ADR-0005).
  - fail-fast loading via :func:`load_settings`, which turns a Pydantic
    `ValidationError` for a missing required field into a :class:`SourceMisconfiguredError`
    naming the *exact* environment variable that is missing (not just the Python
    field name) — this is the error class `mcp_common.errors` maps to
    `ErrorCode.source_misconfigured`.

Secrets are never read from a committed file: only `os.environ` or a file a `*_FILE`
variable points to (ADR-0005). No package may read a repo-committed `.env`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

__all__ = [
    "CommonSettings",
    "SourceSettingsBase",
    "SourceMisconfiguredError",
    "load_settings",
]


class SourceMisconfiguredError(Exception):
    """Raised when required configuration is missing or invalid at startup.

    Carries the *exact* environment variable name(s) so the operator does not have
    to guess (architecture.md Error model: `source_misconfigured` → "Chỉ đúng biến env").
    """

    def __init__(self, missing_vars: list[str], *, source: str | None = None) -> None:
        self.missing_vars = list(missing_vars)
        self.source = source
        joined = ", ".join(self.missing_vars) if self.missing_vars else "(unknown)"
        message = f"Missing or invalid required environment variable(s): {joined}"
        if source:
            message = f"[{source}] {message}"
        super().__init__(message)


class _FileSuffixEnvSource(PydanticBaseSettingsSource):
    """Settings source implementing the `<ENV_VAR>_FILE` convention.

    For every declared field, if the plain env var is absent but `<ENV_VAR>_FILE` is
    set, this source reads the (stripped) file contents and uses that as the value.
    Lower priority than the plain env var so an explicit `MCP_X_TOKEN` always wins
    over a stale `MCP_X_TOKEN_FILE` if both happen to be set.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        import os

        env_prefix = self.config.get("env_prefix") or ""
        env_var = f"{env_prefix}{field_name}".upper()
        file_var = f"{env_var}_FILE"
        file_path = os.environ.get(file_var)
        if not file_path:
            return None, field_name, False
        content = Path(file_path).read_text(encoding="utf-8").strip()
        return content, field_name, False

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, _is_complex = self.get_field_value(field, field_name)
            if value is not None:
                result[key] = value
        return result


class CommonSettings(BaseSettings):
    """Shared `MCP_*` variables every server reads (architecture.md "Config")."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_",
        case_sensitive=False,
        extra="ignore",
    )

    # HTTP timeout budget (ADR-0006 A2): 2 * (connect + read) + backoff = 21s < 25s.
    http_connect_timeout: float = 3.0
    http_read_timeout: float = 7.0
    http_write_timeout: float = 5.0
    http_pool_timeout: float = 5.0
    http_max_retries: int = 1
    http_backoff_base: float = 1.0

    # Deadline for one whole tool call (ADR-0006 A2).
    tool_deadline: float = 25.0

    # Response/query bounds (R5, ADR-0015).
    max_output_bytes: int = 131072
    max_time_range_days: int = 31

    # Logging (ADR-0005): stderr only; MCP_LOG_FILE is optional, never stdout.
    log_level: str = "INFO"
    log_file: str | None = None

    # Redaction (ADR-0015) — turning this off must be an explicit operator action.
    redact_disabled: bool = False

    # Transport (ADR-0002): v1 only supports stdio.
    transport: str = "stdio"

    # Startup credential-gate escape hatch (ADR-0003 A1) — logs one WARN per startup
    # when true; mcp_common.runtime owns emitting that warning.
    allow_unverified_credentials: bool = False

    # Egress allow-list (CHG-003, ADR-0023 §6a) — CSV of host patterns the ingest-pull /
    # model-download path may reach (e.g. `*.atlassian.net,huggingface.co`). **Empty by
    # default = deny all**: a fresh install makes no outbound call until the operator names
    # the hosts. Enforced at the one `mcp_common.egress.check_egress` choke point (L-001).
    egress_allowlist: str = ""


class SourceSettingsBase(BaseSettings):
    """Base class for every per-source `Settings` (Phase 1+).

    Subclasses set their own `model_config = SettingsConfigDict(env_prefix="MCP_<SOURCE>_")`
    and declare fields (`SecretStr` for credentials). This base wires in the
    `*_FILE` fallback source described in the module docstring.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority (highest first): explicit init kwargs > plain env var >
        # `*_FILE` fallback > dotenv > pydantic's own file-secrets convention.
        return (
            init_settings,
            env_settings,
            _FileSuffixEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


def load_settings[T: BaseSettings](settings_cls: type[T], *, source: str | None = None) -> T:
    """Instantiate `settings_cls`, turning a missing/invalid required field into a
    :class:`SourceMisconfiguredError` that names the exact environment variable.

    Fail-fast per ADR-0005: config errors must surface at startup, not on first tool call.
    """
    try:
        return settings_cls()
    except ValidationError as exc:
        prefix = str(settings_cls.model_config.get("env_prefix") or "")
        missing_vars: list[str] = []
        for error in exc.errors():
            field_name = str(error["loc"][0]) if error.get("loc") else "?"
            env_var = f"{prefix}{field_name}".upper()
            missing_vars.append(env_var)
        raise SourceMisconfiguredError(missing_vars, source=source) from exc
