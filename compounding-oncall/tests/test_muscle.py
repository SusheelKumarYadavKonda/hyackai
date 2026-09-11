"""Tests for muscle memory: capture once, replay deterministically."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.muscle import MuscleMemoryStore  # noqa: E402

DECISION = {
    "diagnosis": "orders failed: customer_id null rate 40.0% vs 0.0% known-good.",
    "action": "quarantine_partition",
    "params": {"table": "orders", "partition": "2026-03-04", "notify": "@devon"},
    "chain": ["quarantine_partition", "rerun_job", "notify_owner"],
    "tokens_used": 9140,
}


def test_miss_then_hit(tmp_path):
    store = MuscleMemoryStore(tmp_path / "m.json")
    assert store.lookup("sig_abc") is None

    store.capture("sig_abc", "orders", DECISION)
    assert store.lookup("sig_abc") is not None


def test_replay_costs_zero_tokens(tmp_path):
    store = MuscleMemoryStore(tmp_path / "m.json")
    store.capture("sig_abc", "orders", DECISION)

    replayed = store.replay("sig_abc", "shipments")
    assert replayed["tokens_used"] == 0
    assert replayed["action"] == DECISION["action"]


def test_replay_retargets_the_table(tmp_path):
    """A path captured on one table must apply to another. This is the whole
    reason the signature excludes the table name."""
    store = MuscleMemoryStore(tmp_path / "m.json")
    store.capture("sig_abc", "orders", DECISION)

    replayed = store.replay("sig_abc", "inventory")
    assert replayed["params"]["table"] == "inventory"


def test_capture_is_idempotent(tmp_path):
    """First writer wins: the captured path is the one that was verified."""
    store = MuscleMemoryStore(tmp_path / "m.json")
    store.capture("sig_abc", "orders", DECISION)
    store.capture("sig_abc", "shipments", {**DECISION, "action": "open_ticket"})

    assert store.count() == 1
    assert store.lookup("sig_abc")["action"] == "quarantine_partition"
    assert store.lookup("sig_abc")["captured_from_table"] == "orders"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "m.json"
    MuscleMemoryStore(path).capture("sig_abc", "orders", DECISION)

    assert MuscleMemoryStore(path).lookup("sig_abc") is not None


def test_replay_count_increments(tmp_path):
    store = MuscleMemoryStore(tmp_path / "m.json")
    store.capture("sig_abc", "orders", DECISION)

    store.replay("sig_abc", "shipments")
    store.replay("sig_abc", "inventory")
    assert store.lookup("sig_abc")["replay_count"] == 2


def test_corrupt_store_does_not_raise(tmp_path):
    """A bad state file must never take the demo down."""
    path = tmp_path / "m.json"
    path.write_text("{not json")
    assert MuscleMemoryStore(path).count() == 0


def test_reset_clears(tmp_path):
    store = MuscleMemoryStore(tmp_path / "m.json")
    store.capture("sig_abc", "orders", DECISION)
    store.reset()
    assert store.count() == 0
