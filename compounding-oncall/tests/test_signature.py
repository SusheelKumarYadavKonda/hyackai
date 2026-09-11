"""Tests for the signature logic.

The first test is the one that protects the demo: if the table name leaks into
the signature, every incident is unique, replay never fires, and there is no
compounding curve.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.signature import normalize, sig_type, signature  # noqa: E402

ORDERS_NULL = (
    "[2026-03-04T02:00:11+00:00] job_orders_daily FAILED: NOT NULL constraint "
    "violated on column customer_id in orders: 7994 null values across 20000 "
    "rows (40.0%). First offending row 4471. source='orders_today.parquet'"
)

SHIPMENTS_NULL = (
    "[2026-03-11T02:00:04+00:00] job_shipments_daily FAILED: NOT NULL constraint "
    "violated on column customer_id in shipments: 8012 null values across 20000 "
    "rows (40.1%). First offending row 3902. source='shipments_today.parquet'"
)

ORDERS_VOLUME = (
    "[2026-03-04T02:00:09+00:00] job_orders_daily FAILED: row count check on "
    "orders: got 400 rows, expected at least 1000. Partition volume collapsed. "
    "source='orders_today.parquet'"
)


def test_same_failure_mode_different_table_shares_signature():
    """The load-bearing property. Without this there is no curve."""
    assert signature(ORDERS_NULL, "orders") == signature(SHIPMENTS_NULL, "shipments")


def test_table_name_absent_from_signature_input():
    """Guard against a well-meaning refactor reintroducing the table name."""
    assert signature(ORDERS_NULL, "orders", include_table=False) != signature(
        ORDERS_NULL, "orders", include_table=True
    )


def test_different_failure_modes_differ():
    assert signature(ORDERS_NULL, "orders") != signature(ORDERS_VOLUME, "orders")


def test_table_name_is_stripped_from_error_body():
    """Real job output names the table in the job id and the assertion text, so
    normalisation has to remove it there too, not just from the hash input."""
    normalised = normalize(ORDERS_NULL, "orders")
    assert "orders" not in normalised
    assert "<table>" in normalised


def test_timestamps_and_counts_are_stripped():
    normalised = normalize(ORDERS_NULL, "orders")
    assert "2026-03-04" not in normalised
    assert "7994" not in normalised
    assert "4471" not in normalised
    assert "orders_today.parquet" not in normalised
    assert "not null constraint" in normalised


def test_signature_is_stable_across_runs():
    assert signature(ORDERS_NULL, "orders") == signature(ORDERS_NULL, "orders")


def test_sig_type_classification():
    assert sig_type(ORDERS_NULL) == "NULL_FLOOD"
    assert sig_type(ORDERS_VOLUME) == "VOLUME_COLLAPSE"
    assert sig_type("something entirely unfamiliar happened") == "UNKNOWN"
