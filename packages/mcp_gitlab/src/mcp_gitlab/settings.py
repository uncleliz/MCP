"""mcp-gitlab settings (`MCP_GITLAB_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_PATH_DENY", "Settings"]

# ADR-0015 A1: deny-glob for paths that routinely hold secrets. Broadened for R-002:
# the original list only matched names *ending* in a sensitive suffix/word (fnmatch,
# no leading `*`), so `.env.local`/`.env.production`, `id_ed25519`, `*.key`, `*.p12`,
# `.npmrc`, `.netrc` and `*.tfstate` all slipped through. Every pattern below is
# checked against both the full lowercased path and its basename (see `is_path_denied`
# / `path_denied` in mcp_ingest.pipeline.redact, which reuses this exact list).
DEFAULT_PATH_DENY = (
    "*.env,*.env.*,.env,*secret*,*credential*,*.pem,id_rsa*,id_dsa*,id_ecdsa*,id_ed25519*,"
    "*.key,*.pfx,*.p12,*.jks,.npmrc,.netrc,*.tfstate,*.tfstate.*,*.tfvars"
)


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_GITLAB_", case_sensitive=False, extra="ignore"
    )

    base_url: str
    private_token: SecretStr
    # Comma-separated fnmatch globs matched (case-insensitively) against the full path and
    # the basename. Setting it empty disables the deny-glob: an explicit operator action.
    path_deny: str = DEFAULT_PATH_DENY
    # Hard cap on raw bytes read off the wire for `get_job_trace` (R-001): job traces are
    # tailed/truncated for tool output, but the *download* itself was previously unbounded —
    # a multi-hundred-MB trace would be buffered whole before any truncation ever happened.
    # Independent of `MaxBytesField`/`MCP_MAX_OUTPUT_BYTES` (the tool *output* budget).
    max_job_trace_bytes: int = 10_485_760  # 10 MiB

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
