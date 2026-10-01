"""mcp-kafka settings (`MCP_KAFKA_*`).

`socket.timeout.ms` / `metadata.request.timeout.ms` are fixed in code (ADR-0006 A2), not
settings, to keep the NFR-002 budget invariant intact.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["Settings"]


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_KAFKA_", case_sensitive=False, extra="ignore"
    )

    # Comma-separated host:port list.
    bootstrap_servers: str
    security_protocol: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = "PLAINTEXT"
    sasl_mechanism: Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"] = "SCRAM-SHA-512"
    sasl_username: str | None = None
    sasl_password: SecretStr | None = None
    ssl_ca_location: str | None = None
    client_id: str = "mcp-kafka-readonly"

    @field_validator("bootstrap_servers")
    @classmethod
    def _validate_bootstrap(cls, value: str) -> str:
        servers = [s.strip() for s in value.split(",") if s.strip()]
        if not servers or any("://" in s for s in servers):
            raise ValueError("must be a comma-separated host:port list (no scheme)")
        return ",".join(servers)

    @model_validator(mode="after")
    def _sasl_needs_credentials(self) -> Settings:
        if self.security_protocol.startswith("SASL") and not (
            self.sasl_username and self.sasl_password
        ):
            raise ValueError(
                "MCP_KAFKA_SASL_USERNAME and MCP_KAFKA_SASL_PASSWORD are required for SASL"
            )
        return self

    @property
    def first_host(self) -> str:
        return self.bootstrap_servers.split(",")[0]
