# Runbook: quarantining a bad partition

Quarantine means: stop consuming the suspect partition, point consumers at the
last known-good, and leave the bad data in place for investigation.

Do not delete it. Do not overwrite it. The partition is evidence.

After quarantining, rerun the consuming job against the known-good partition and
confirm a clean exit before closing the incident. An incident that was never
verified is an incident that will reopen.
