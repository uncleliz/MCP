"""Report models of the CLI, mirroring `components.schemas` of api-contract.yaml.

T-056 ships `MigrationResult`; the other report schemas arrive with the CLI shell (T-068).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MigrationResult"]


class MigrationResult(BaseModel):
    """`components.schemas.MigrationResult`."""

    model_config = ConfigDict(extra="forbid")

    applied: list[str]
    already_applied: list[str] = Field(default_factory=list)
    current_version: str | None
    dry_run: bool = False
