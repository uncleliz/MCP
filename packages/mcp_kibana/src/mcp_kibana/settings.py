"""mcp-kibana settings (`MCP_KIBANA_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["Settings"]


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_KIBANA_", case_sensitive=False, extra="ignore"
    )

    base_url: str
    username: str | None = None
    password: SecretStr | None = None
    verify_certs: bool = True

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _username_and_password_go_together(self) -> Settings:
        if (self.username is None) != (self.password is None):
            raise ValueError("MCP_KIBANA_USERNAME and MCP_KIBANA_PASSWORD must be set together")
        return self
