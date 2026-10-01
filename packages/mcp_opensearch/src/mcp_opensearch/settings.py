"""mcp-opensearch settings (`MCP_OPENSEARCH_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_DENY_ROLES", "Settings", "dsl_enabled_from_env"]

# Security-plugin roles that can write or administer the cluster: the startup check refuses
# to serve if the credential maps to any of them (ADR-0003 A1).
DEFAULT_DENY_ROLES = "all_access,security_manager,admin"


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_OPENSEARCH_", case_sensitive=False, extra="ignore"
    )

    # Comma-separated node URLs, e.g. https://os1.internal:9200,https://os2.internal:9200
    hosts: str
    username: str | None = None
    password: SecretStr | None = None
    verify_certs: bool = True
    # `opensearch_search_dsl` is an escape hatch: not registered unless explicitly enabled.
    allow_dsl: bool = False
    deny_roles: str = DEFAULT_DENY_ROLES

    @field_validator("hosts")
    @classmethod
    def _validate_hosts(cls, value: str) -> str:
        hosts = [h.strip().rstrip("/") for h in value.split(",") if h.strip()]
        if not hosts or any(not h.startswith(("https://", "http://")) for h in hosts):
            raise ValueError("must be a comma-separated list of http(s):// URLs")
        return ",".join(hosts)

    @model_validator(mode="after")
    def _username_and_password_go_together(self) -> Settings:
        if (self.username is None) != (self.password is None):
            raise ValueError("MCP_OPENSEARCH_USERNAME and MCP_OPENSEARCH_PASSWORD go together")
        return self

    @property
    def host_list(self) -> list[str]:
        return self.hosts.split(",")

    @property
    def denied_roles(self) -> set[str]:
        return {r.strip() for r in self.deny_roles.split(",") if r.strip()}


class _DslFlag(SourceSettingsBase):
    """Only the feature flag, so `build_server()` can decide which tools to register
    before (and without) any credential being configured."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_OPENSEARCH_", case_sensitive=False, extra="ignore"
    )

    allow_dsl: bool = False


def dsl_enabled_from_env() -> bool:
    return _DslFlag().allow_dsl
