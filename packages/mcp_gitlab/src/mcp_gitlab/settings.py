"""mcp-gitlab settings (`MCP_GITLAB_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_PATH_DENY", "Settings"]

# ADR-0015 A1: deny-glob for paths that routinely hold secrets.
DEFAULT_PATH_DENY = "*.env,*secret*,*credential*,*.pem,id_rsa*"


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_GITLAB_", case_sensitive=False, extra="ignore"
    )

    base_url: str
    private_token: SecretStr
    # Comma-separated fnmatch globs matched (case-insensitively) against the full path and
    # the basename. Setting it empty disables the deny-glob: an explicit operator action.
    path_deny: str = DEFAULT_PATH_DENY

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with http:// or https://")
        return value

    @property
    def deny_globs(self) -> list[str]:
        return [part.strip() for part in self.path_deny.split(",") if part.strip()]
