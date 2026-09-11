"""Tests that the memory layers actually change the chosen action.

Without these, Cognee and HydraDB are decorative: retrieved, printed, ignored.
The precedence being asserted is proven fix > prescribed fix > default rule.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.rocketride_adapter import _reason  # noqa: E402

INCIDENT = {
    "incident_id": "t1",
    "job_id": "job_orders_daily",
    "table": "orders",
    "run_ts": "2026-03-04T02:00:00+00:00",
    "error_text": "NOT NULL constraint violated on column customer_id",
    "exit_code": 1,
    "parquet_path": "data/warehouse/orders_today.parquet",
}

DIAG_NULL = {
    "metrics": {"delta_pp": 40.0},
    "failed_check": "null_rate_delta",
    "detail": "customer_id null rate 40.0% today vs 0.0% last known-good",
}

DIAG_CLEAN = {"metrics": {}, "failed_check": None, "detail": "no check failed"}


def ctx(corpus=None, fixes=None):
    return {
        "upstream_tables": [],
        "past_fixes": fixes or [],
        "corpus_recall": corpus or [],
        "owner": "@priya",
        "node_count": 36,
    }


def test_default_rule_when_memory_is_empty():
    """Cold start with nothing recalled falls back to the diagnostic rule."""
    _, action, params = _reason(INCIDENT, ctx(), DIAG_NULL)
    assert action == "quarantine_partition"
    assert params["rationale"] == "selected by diagnostic rule"


def test_runbook_recall_overrides_the_default():
    """A documented fix beats the built-in heuristic. This is what makes Cognee
    load-bearing rather than decorative."""
    corpus = [{
        "title": "runbook_null_constraint.md",
        "score": 9,
        "excerpt": "Quarantine the affected partition before rerunning.",
    }]
    _, action, params = _reason(INCIDENT, ctx(corpus=corpus), DIAG_CLEAN)

    # Default for a clean diagnostic would be open_ticket; the runbook wins.
    assert action == "quarantine_partition"
    assert "cognee" in params["rationale"]


def test_proven_fix_outranks_the_runbook():
    """A fix we have actually applied beats one we have only read about."""
    corpus = [{"title": "rb.md", "score": 5, "excerpt": "open a ticket for triage"}]
    fixes = [{"action": "quarantine_partition", "success_count": 3}]
    _, action, params = _reason(INCIDENT, ctx(corpus=corpus, fixes=fixes), DIAG_NULL)

    assert action == "quarantine_partition"
    assert "hydradb" in params["rationale"]


def test_most_successful_fix_is_chosen():
    fixes = [
        {"action": "open_ticket", "success_count": 1},
        {"action": "rerun_job", "success_count": 7},
    ]
    _, action, _ = _reason(INCIDENT, ctx(fixes=fixes), DIAG_NULL)
    assert action == "rerun_job"


def test_unactionable_corpus_does_not_override():
    """Recalled prose that prescribes nothing must leave the default standing,
    so noise cannot hijack the decision."""
    corpus = [{
        "title": "pm_generic.md",
        "score": 2,
        "excerpt": "Impact was limited. Detection came from the mart assertion.",
    }]
    _, action, params = _reason(INCIDENT, ctx(corpus=corpus), DIAG_NULL)
    assert action == "quarantine_partition"
    assert params["rationale"] == "selected by diagnostic rule"


def test_unknown_action_in_past_fixes_is_ignored():
    """Never execute an action name we do not recognise."""
    fixes = [{"action": "drop_table", "success_count": 99}]
    _, action, params = _reason(INCIDENT, ctx(fixes=fixes), DIAG_NULL)
    assert action == "quarantine_partition"
    assert "hydradb" not in params["rationale"]


def test_rationale_is_recorded_in_the_diagnosis():
    """The diagnosis must say why, so the demo can show the reason on screen."""
    corpus = [{"title": "rb.md", "score": 9, "excerpt": "Quarantine the partition."}]
    diagnosis, _, _ = _reason(INCIDENT, ctx(corpus=corpus), DIAG_NULL)
    assert "runbook recall" in diagnosis
