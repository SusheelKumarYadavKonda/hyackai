# Runbook: walking lineage during an incident

Start at the failing table. Find the job that writes it. List the tables that
job reads. Those are your hop-one candidates. Repeat.

Stop at three hops. Beyond that the causal link is usually too weak to act on
and you are better off asking the owning team.

Cross-reference each upstream table against the schema change log, filtered to
the last week. A change older than the last successful run is rarely the cause.
