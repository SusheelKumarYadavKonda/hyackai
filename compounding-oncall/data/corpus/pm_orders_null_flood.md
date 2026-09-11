# Postmortem: orders_daily null flood

**Impact:** orders_daily failed for one run cycle. Downstream revenue reporting
was stale for roughly three hours.

**Root cause:** the CDC connector for raw_customers_cdc renamed `cust_id` to
`customer_id_v2`. Our staging view still selected `cust_id`, which silently
became null rather than raising. orders_daily then aborted on its NOT NULL
assertion.

**Detection:** the mart job's own assertion. No upstream alert fired, which is
the real gap.

**Resolution:** quarantined the affected partition, reran against last
known-good, and paged @priya on commerce-data to correct the staging view.

**Lesson:** a rename upstream is a null flood downstream. Check the schema change
log before diagnosing the mart.
