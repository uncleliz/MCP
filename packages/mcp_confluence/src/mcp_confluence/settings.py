"""mcp-confluence settings (`MCP_CONFLUENCE_*`). Locked decision: Confluence **Cloud**
(email + API token, Basic auth, `/wiki/rest/api/...`). `flavor` exists so Server/DC can be
added later without renaming the variable; any other value fails fast today."""

from __future__ import annotations

from typing import ClassVar, Literal

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["Settings"]


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_CONFLUENCE_", case_sensitive=False, extra="ignore"
    )

    base_url: str
    flavor: Literal["cloud"] = "cloud"
    email: str
    api_token: SecretStr

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with http:// or https://")
        return value
