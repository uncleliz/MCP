"""The ingest pipeline: normalize -> redact -> chunk -> embed -> persist, with checkpoints,
per-source failure isolation, retry-failed and the tombstone reconcile (FR-012)."""
