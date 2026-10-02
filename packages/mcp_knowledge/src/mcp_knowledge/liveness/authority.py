"""Source-authority config (spec §42, FR-018/AC-002, migration 0008 ``kb.source_authority`` +
``kb.freshness_horizon``).

Which source is *authoritative* for a given kind of fact — runtime/config → GitLab, architecture →
Confluence, current work status → Jira, code behaviour → GitLab — is **configuration read from the
database**, NOT a string hardcoded in any LLM prompt (spec §42 "must not be hardcoded into the LLM
prompt alone"). The grounding gate / reconciler reads these rows to build the ``authority_note`` it
attaches to a ``CONFLICT`` and to pick each fact type's freshness horizon.

The config is loaded once per request from the read-only domain tx. If the config tables are absent
(an older DB that has not run migration 0008) the loader falls back to
:data:`DEFAULT_SOURCE_AUTHORITY` and :data:`DEFAULT_FRESHNESS_HORIZON` — the *same values migration
0008 seeds* — so the Live path
degrades gracefully instead of failing. The defaults mirror the migration exactly; they are not an
independent source of truth, only a safety net (and they are reported via ``loaded_from_db`` so a
caller can tell whether the DB or the fallback answered).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "DEFAULT_FRESHNESS_HORIZON",
    "DEFAULT_SOURCE_AUTHORITY",
    "SourceAuthorityConfig",
    "load_source_authority",
]

#: Mirrors the ``kb.source_authority`` seed in migration 0008 (fact_type → authoritative source).
DEFAULT_SOURCE_AUTHORITY: dict[str, str] = {
    "runtime_config": "gitlab",
    "architecture": "confluence",
    "current_work_status": "jira",
    "code_behavior": "gitlab",
}

#: Mirrors the ``kb.freshness_horizon`` seed in migration 0008 (fact_type → hours; None = never).
DEFAULT_FRESHNESS_HORIZON: dict[str, int | None] = {
    "runtime_config": 168,
    "architecture": 2160,
    "current_work_status": 24,
    "code_behavior": 720,
}

#: Rationale strings mirroring the migration seed, used in the authority_note when the DB does not
#: supply one (older rows) — still config, never invented per fact.
_DEFAULT_RATIONALE: dict[str, str] = {
    "runtime_config": "Runtime/config values live in the repo (GitLab).",
    "architecture": "Architecture/design docs are authored in Confluence.",
    "current_work_status": "Live work status (sprint/issue) is authoritative in Jira.",
    "code_behavior": "Code behaviour is defined by the source in GitLab.",
}


class _ReadTx(Protocol):
    async def fetch(
        self, name: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SourceAuthorityConfig:
    """The loaded source-authority + freshness-horizon config for one request."""

    authority: dict[str, str]
    rationale: dict[str, str]
    horizon_hours: dict[str, int | None]
    loaded_from_db: bool

    def authoritative_source(self, fact_type: str) -> str | None:
        """The source configured as authoritative for ``fact_type`` (``None`` if unknown)."""
        return self.authority.get(fact_type)

    def horizon_for(self, fact_type: str) -> int | None:
        """The freshness horizon (hours) for ``fact_type``; ``None`` means never stale/unknown."""
        return self.horizon_hours.get(fact_type)

    def authority_note(self, fact_type: str) -> str | None:
        """Build the human-readable ``authority_note`` for a CONFLICT on ``fact_type``.

        Derived entirely from config (DB rows or the migration-mirroring defaults) — never a string
        baked into a prompt (spec §42). Returns ``None`` when the fact type has no configured
        authority, so the reconciler can expose the conflict *without* asserting a winner.
        """
        source = self.authority.get(fact_type)
        if source is None:
            return None
        reason = self.rationale.get(fact_type) or _DEFAULT_RATIONALE.get(fact_type)
        base = (
            f"For '{fact_type}', the authoritative source is '{source}' "
            "per configured source authority"
        )
        return f"{base}: {reason}" if reason else f"{base}."


async def load_source_authority(tx: _ReadTx) -> SourceAuthorityConfig:
    """Load the source-authority + freshness-horizon config from the read-only domain tx.

    Falls back to the migration-0008-mirroring defaults when the config tables are absent (older
    DB) so the Live path keeps working. The fallback is signalled by ``loaded_from_db=False``.
    """
    try:
        authority_rows = await tx.fetch("source_authority_all")
        horizon_rows = await tx.fetch("freshness_horizon_all")
    except Exception:  # noqa: BLE001 — config missing/unreadable degrades to defaults, never fails
        return SourceAuthorityConfig(
            authority=dict(DEFAULT_SOURCE_AUTHORITY),
            rationale=dict(_DEFAULT_RATIONALE),
            horizon_hours=dict(DEFAULT_FRESHNESS_HORIZON),
            loaded_from_db=False,
        )

    if not authority_rows and not horizon_rows:
        return SourceAuthorityConfig(
            authority=dict(DEFAULT_SOURCE_AUTHORITY),
            rationale=dict(_DEFAULT_RATIONALE),
            horizon_hours=dict(DEFAULT_FRESHNESS_HORIZON),
            loaded_from_db=False,
        )

    authority = {
        str(r["fact_type"]): str(r["authoritative_source"])
        for r in authority_rows
        if r.get("fact_type") and r.get("authoritative_source")
    }
    rationale = {
        str(r["fact_type"]): str(r["rationale"])
        for r in authority_rows
        if r.get("fact_type") and r.get("rationale")
    }
    horizon = {
        str(r["fact_type"]): (
            int(r["horizon_hours"]) if r.get("horizon_hours") is not None else None
        )
        for r in horizon_rows
        if r.get("fact_type")
    }
    return SourceAuthorityConfig(
        authority=authority or dict(DEFAULT_SOURCE_AUTHORITY),
        rationale=rationale or dict(_DEFAULT_RATIONALE),
        horizon_hours=horizon or dict(DEFAULT_FRESHNESS_HORIZON),
        loaded_from_db=True,
    )
