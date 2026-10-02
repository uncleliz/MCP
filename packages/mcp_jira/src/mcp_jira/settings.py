"""mcp-jira settings (`MCP_JIRA_*`, ADR-0019).

Two Jira API flavors share one tool surface:

* **Cloud** — `/rest/api/3`, pagination by opaque `nextPageToken`.
* **Server / Data Center** — `/rest/api/2`, pagination by `startAt`/`maxResults`.

`flavor` is `MCP_JIRA_FLAVOR` with an `auto` default that infers the flavor from the base URL
(`*.atlassian.net` => Cloud, anything else => Server/DC). The Agile endpoints
(`/rest/agile/1.0/...`) are identical across flavors. Auth is Basic (email + API token) on
Cloud and Bearer (PAT) on Server/DC; the resolved scheme is exposed to `client.py` so the
transport layer stays flavor-agnostic.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["Flavor", "ResolvedFlavor", "Settings"]

Flavor = Literal["auto", "cloud", "server"]
ResolvedFlavor = Literal["cloud", "server"]

# API version + pagination style per resolved flavor (ADR-0019).
_API_VERSION: dict[ResolvedFlavor, str] = {"cloud": "3", "server": "2"}


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_JIRA_", case_sensitive=False, extra="ignore"
    )

    base_url: str
    flavor: Flavor = "auto"
    # Cloud uses Basic auth (email + API token); Server/DC uses a bearer PAT. `email` is required
    # only when the resolved flavor is Cloud — validated in `_require_cloud_email`.
    email: str | None = None
    token: SecretStr

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with http:// or https://")
        return value

    @property
    def resolved_flavor(self) -> ResolvedFlavor:
        """`flavor` with `auto` resolved from the base URL (atlassian.net => Cloud)."""
        if self.flavor == "auto":
            return "cloud" if "atlassian.net" in self.base_url.lower() else "server"
        return self.flavor

    @property
    def api_version(self) -> str:
        """`3` for Cloud, `2` for Server/DC — the `/rest/api/{v}` segment (ADR-0019)."""
        return _API_VERSION[self.resolved_flavor]

    @model_validator(mode="after")
    def _require_cloud_email(self) -> Settings:
        if self.resolved_flavor == "cloud" and not self.email:
            raise ValueError("email is required for Jira Cloud (Basic auth email + API token)")
        return self
