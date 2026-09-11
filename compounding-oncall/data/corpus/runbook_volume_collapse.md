# Runbook: partition volume collapse

A partition landing far below its usual size is nearly always an ingestion
problem, not a transformation problem.

Compare today's row count against the trailing seven-day median rather than
against a fixed threshold; traffic is seasonal and fixed thresholds page you
every Sunday. Anything below roughly a fifth of the median is a genuine
collapse.

Check whether the upstream extract completed. A partially written partition and
a genuinely empty day look identical downstream, so confirm the source job's
exit status before deciding.

Quarantine, then rerun against last known-good while the upstream team
investigates.
