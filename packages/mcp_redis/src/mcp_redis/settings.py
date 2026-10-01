"""mcp-redis settings (`MCP_REDIS_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_KEY_DENY", "Settings"]

# ADR-0015 / R4: key-name globs that routinely hold secrets.
DEFAULT_KEY_DENY = "*.env,*secret*,*credential*,*.pem,id_rsa*"


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_REDIS_", case_sensitive=False, extra="ignore"
    )

    # e.g. redis://mcp_ro@host:6379/0 — the ACL user belongs in the URL, the password in
    # MCP_REDIS_PASSWORD (or MCP_REDIS_PASSWORD_FILE) so it never sits in a config file.
    url: str
    password: SecretStr | None = None
    # Comma-separated fnmatch globs (case-insensitive). Empty disables the deny-glob: an
    # explicit operator action.
    key_deny: str = DEFAULT_KEY_DENY

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("must start with redis:// or rediss://")
        return value

    @property
    def deny_globs(self) -> list[str]:
        return [part.strip() for part in self.key_deny.split(",") if part.strip()]
