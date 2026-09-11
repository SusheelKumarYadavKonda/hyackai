# Runbook: NOT NULL constraint failures

When a mart job aborts on a NOT NULL constraint, do not patch the mart. The mart
is telling the truth; something upstream stopped supplying the column.

1. Identify the column named in the assertion.
2. Walk the lineage up to the CDC source that feeds it. Two hops is typical,
   three when a staging view sits in between.
3. Check the schema change log for that column in the last seven days. A rename
   on the source side surfaces as nulls downstream, not as an error.
4. Quarantine the affected partition before rerunning. Reprocessing bad input
   just reproduces the failure and doubles the alert noise.
5. Page the owning team for the upstream table, not the mart team.

Typical cause: a CDC connector column rename that was not propagated.
