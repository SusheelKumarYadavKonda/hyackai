# Postmortem: inventory_daily, third null incident this quarter

**Impact:** inventory_daily failed on the same assertion for the third time in
a quarter. Cumulative on-call time spent: roughly two hours, nearly all of it
rediscovering a cause we had already documented twice.

**Root cause:** same class as the orders incident. A column rename in
raw_inventory_snapshots propagated as nulls.

**Detection:** NOT NULL assertion on customer_id.

**Resolution:** identical to the previous two: quarantine, rerun, notify
@saral.

**Lesson:** the fix was already written down. The cost was not the diagnosis, it
was that nobody could find the previous diagnosis. This is the problem worth
solving.
