# Postmortem: shipments partition arrived nearly empty

**Impact:** shipments_daily aborted; carrier SLA dashboards showed a false
overnight gap.

**Root cause:** the raw_shipments_cdc extract was throttled by the carrier API
and wrote a partial partition. Row count came in at about two percent of the
usual daily volume.

**Detection:** row count assertion against the seven-day median.

**Resolution:** quarantined the partition, reran against known-good, and
notified @tomo on logistics-data to re-extract once the rate limit cleared.

**Lesson:** partial extracts and empty days are indistinguishable downstream.
Always confirm upstream exit status.
