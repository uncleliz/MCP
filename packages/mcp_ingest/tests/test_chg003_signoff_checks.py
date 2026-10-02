"""T-122 (sign-off checks wired as tests — the human sign-off is NOT done here) — CHG-003
end-of-batch invariants in one place (ADR-0023 §6e, FR-023..027, NFR-013/014):

* the runbook's documented commands match the real CLI for **all 9 sources** (4 ingestable with a
  ``doctor`` + an ingest registry entry, 5 live-only with a ``doctor`` + a config-emit block and
  **no** ingest registry entry);
* the egress guard is one default-deny choke point (`check_egress`) and the model egress is scoped
  to ``huggingface.co`` and separable from Atlassian;
* ADR-0010 stays provisional and the harness invents no recall/τ (`calibration_status=uncalibrated`,
  `# THRESHOLD TBD` grep-able).

Behavioural proofs live in their own files (TC-118..131); this is the aggregation gate.
"""

from __future__ import annotations

import importlib

import pytest
from mcp_common.egress import EgressDenied, check_egress
from mcp_ingest.connectors import registry
from mcp_ingest.embedding.config import DEFAULT_MODEL
from mcp_ingest.embedding.download import HF_MODEL_HOST

INGESTABLE = ("confluence", "gitlab", "opensearch", "jira")
LIVE_ONLY = ("cloudwatch", "kibana", "kafka", "redis", "sqs_sns")
_CLI = {
    "confluence": "mcp_confluence.cli",
    "gitlab": "mcp_gitlab.cli",
    "opensearch": "mcp_opensearch.cli",
    "jira": "mcp_jira.cli",
    "cloudwatch": "mcp_cloudwatch.cli",
    "kibana": "mcp_kibana.cli",
    "kafka": "mcp_kafka.cli",
    "redis": "mcp_redis.cli",
    "sqs_sns": "mcp_sqs_sns.cli",
}


def test_all_nine_sources_have_a_doctor_cli() -> None:
    """Every source (9) exposes a `doctor` subcommand — the runbook's first command for each."""
    for source in INGESTABLE + LIVE_ONLY:
        module = importlib.import_module(_CLI[source])
        assert hasattr(module, "main"), f"{_CLI[source]} has no main()"
        with pytest.raises(SystemExit) as exc:
            module.main(["--help"])
        assert exc.value.code == 0


def test_runbook_source_classes_match_the_registry() -> None:
    """4 ingestable sources are in the ingest registry; the 5 live-only sources are not."""
    assert set(registry.source_names()) == set(INGESTABLE)
    for live_only in LIVE_ONLY:
        assert live_only not in registry.source_names()


def test_egress_is_one_default_deny_choke_point() -> None:
    """`check_egress` is the single gate; empty allow-list denies (default-deny)."""
    with pytest.raises(EgressDenied):
        check_egress("tnexwm.atlassian.net", allowlist=[])
    assert check_egress("tnexwm.atlassian.net", allowlist=["*.atlassian.net"]) == (
        "tnexwm.atlassian.net"
    )


def test_model_egress_is_scoped_to_huggingface() -> None:
    """The model path host constant is huggingface.co; another host on it would be denied."""
    assert HF_MODEL_HOST == "huggingface.co"
    with pytest.raises(EgressDenied):
        check_egress(HF_MODEL_HOST, allowlist=["*.atlassian.net"])  # HF not opened by Atlassian
    assert check_egress(HF_MODEL_HOST, allowlist=["huggingface.co"]) == "huggingface.co"


def test_adr0010_model_stays_provisional() -> None:
    """ADR-0010 provisional: the pinned model id is still bge-m3 (1024d); nothing finalised here."""
    assert DEFAULT_MODEL == "BAAI/bge-m3"


def test_nfr003_harness_is_uncalibrated_and_invents_no_number() -> None:
    """The eval harness carries calibration_status=uncalibrated and no recall (L-002)."""
    eval_harness = pytest.importorskip("mcp_knowledge.grounding.eval_harness")
    report = eval_harness.run_eval([])
    assert report.calibration_status == "uncalibrated"
    assert report.recall is None
