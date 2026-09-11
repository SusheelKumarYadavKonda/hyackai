# Postmortem: recurring CDC rename pattern

Across three separate incidents this quarter, the same shape recurred: an
upstream CDC source renames a column, a staging view keeps selecting the old
name, the column becomes null, and a downstream mart aborts on its NOT NULL
assertion two or three hops later.

Each time, on-call spent between thirty and fifty minutes re-establishing the
same causal chain.

The diagnosis is mechanical once you know the shape: check the schema change log
for the upstream tables, find the rename, quarantine, rerun, notify the owner.

**Lesson:** this class of failure does not need a human. It needs memory.
