#!/usr/bin/env python3
"""Generate all three data tiers, deterministically.

    tier 1  data/corpus/       runbooks, postmortems, slack threads, schema log
    tier 2  data/warehouse/    parquet: clean + corrupted variants
    tier 3  data/incidents.json + data/lineage.json

Seeded so the whole warehouse can be wiped and rebuilt in seconds mid-sprint.

Row counts are deliberately modest. Every diagnostic is relative -- null-rate
delta and ratio-to-baseline -- so absolute volume proves nothing extra, and
hotdata is a cloud service where every row is upload time.

Usage:
    python scripts/make_data.py --seed 42
    python scripts/make_data.py --rows 20000
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import (  # noqa: E402
    CORPUS_DIR,
    DATA_DIR,
    INCIDENTS_FILE,
    LINEAGE_FILE,
    TABLES,
    WAREHOUSE_DIR,
    ensure_dirs,
)

OWNERS = {
    "orders": ("Priya Raman", "@priya", "commerce-data"),
    "shipments": ("Tom Okafor", "@tomo", "logistics-data"),
    "inventory": ("Sara Lindqvist", "@saral", "supply-data"),
}

UPSTREAM = {
    "orders": ["raw_orders_cdc", "raw_customers_cdc"],
    "shipments": ["raw_shipments_cdc", "raw_carriers"],
    "inventory": ["raw_inventory_snapshots", "raw_warehouses"],
}


# --------------------------------------------------------------------------
# Tier 1 -- corpus
# --------------------------------------------------------------------------

RUNBOOKS = [
    ("runbook_null_constraint.md", """# Runbook: NOT NULL constraint failures

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
"""),
    ("runbook_volume_collapse.md", """# Runbook: partition volume collapse

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
"""),
    ("runbook_quarantine.md", """# Runbook: quarantining a bad partition

Quarantine means: stop consuming the suspect partition, point consumers at the
last known-good, and leave the bad data in place for investigation.

Do not delete it. Do not overwrite it. The partition is evidence.

After quarantining, rerun the consuming job against the known-good partition and
confirm a clean exit before closing the incident. An incident that was never
verified is an incident that will reopen.
"""),
    ("runbook_lineage_walk.md", """# Runbook: walking lineage during an incident

Start at the failing table. Find the job that writes it. List the tables that
job reads. Those are your hop-one candidates. Repeat.

Stop at three hops. Beyond that the causal link is usually too weak to act on
and you are better off asking the owning team.

Cross-reference each upstream table against the schema change log, filtered to
the last week. A change older than the last successful run is rarely the cause.
"""),
]

POSTMORTEMS = [
    ("pm_orders_null_flood.md", """# Postmortem: orders_daily null flood

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
"""),
    ("pm_shipments_volume.md", """# Postmortem: shipments partition arrived nearly empty

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
"""),
    ("pm_inventory_repeat.md", """# Postmortem: inventory_daily, third null incident this quarter

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
"""),
    ("pm_cdc_rename_pattern.md", """# Postmortem: recurring CDC rename pattern

Across three separate incidents this quarter, the same shape recurred: an
upstream CDC source renames a column, a staging view keeps selecting the old
name, the column becomes null, and a downstream mart aborts on its NOT NULL
assertion two or three hops later.

Each time, on-call spent between thirty and fifty minutes re-establishing the
same causal chain.

The diagnosis is mechanical once you know the shape: check the schema change log
for the upstream tables, find the rename, quarantine, rerun, notify the owner.

**Lesson:** this class of failure does not need a human. It needs memory.
"""),
]

SLACK_THREADS = [
    ("thread_orders_debug.json", [
        {"user": "priya", "ts": "09:14", "message": "orders_daily red again. NOT NULL on customer_id."},
        {"user": "devon", "ts": "09:15", "message": "mart problem or upstream?"},
        {"user": "priya", "ts": "09:17", "message": "upstream. cust_id got renamed in the customers CDC feed, staging view never caught up. nulls all the way down."},
        {"user": "devon", "ts": "09:18", "message": "same as last month then"},
        {"user": "priya", "ts": "09:19", "message": "same as last month. quarantining the partition and rerunning off known-good."},
        {"user": "devon", "ts": "09:21", "message": "we have a postmortem for this exact thing somewhere"},
        {"user": "priya", "ts": "09:22", "message": "we have three."},
    ]),
    ("thread_shipments_volume.json", [
        {"user": "tomo", "ts": "02:41", "message": "shipments partition is tiny. like 2% of normal."},
        {"user": "ana", "ts": "02:44", "message": "carrier API rate limited us overnight, extract wrote a partial file"},
        {"user": "tomo", "ts": "02:45", "message": "so not an empty day, a truncated one. row count check caught it at least."},
        {"user": "ana", "ts": "02:47", "message": "quarantine and rerun off yesterday?"},
        {"user": "tomo", "ts": "02:48", "message": "yep. re-extracting once the limit resets."},
    ]),
    ("thread_oncall_frustration.json", [
        {"user": "saral", "ts": "23:52", "message": "inventory_daily null constraint. third time."},
        {"user": "devon", "ts": "23:55", "message": "what was the fix last time"},
        {"user": "saral", "ts": "23:58", "message": "that is the whole problem, I know we fixed it, I cannot find how"},
        {"user": "devon", "ts": "00:03", "message": "found a postmortem. quarantine + rerun + page upstream owner."},
        {"user": "saral", "ts": "00:04", "message": "45 minutes to rediscover something we already knew. cool."},
    ]),
]


def write_corpus() -> int:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0

    for name, body in RUNBOOKS + POSTMORTEMS:
        (CORPUS_DIR / name).write_text(body)
        count += 1

    for name, messages in SLACK_THREADS:
        (CORPUS_DIR / name).write_text(json.dumps(messages, indent=2))
        count += 1

    # Schema change log -- the causal evidence the lineage walk looks for.
    now = datetime.now(timezone.utc)
    rows = ["change_id,ts,table,column,description,author"]
    changes = [
        ("sc_001", 2, "raw_customers_cdc", "customer_id",
         "renamed cust_id -> customer_id_v2 in CDC connector", "devon"),
        ("sc_002", 3, "raw_shipments_cdc", "customer_id",
         "carrier feed column remapped during connector upgrade", "ana"),
        ("sc_003", 1, "raw_inventory_snapshots", "customer_id",
         "snapshot export dropped cust_id alias", "devon"),
        ("sc_004", 12, "raw_orders_cdc", "order_total",
         "widened decimal precision (outside incident window)", "priya"),
    ]
    for change_id, days_ago, table, column, description, author in changes:
        ts = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        rows.append(f"{change_id},{ts},{table},{column},\"{description}\",{author}")
    (CORPUS_DIR / "schema_changes.csv").write_text("\n".join(rows) + "\n")
    count += 1

    return count


# --------------------------------------------------------------------------
# Tier 2 -- warehouse parquet
# --------------------------------------------------------------------------


def write_warehouse(rows: int, seed: int) -> list[str]:
    try:
        import duckdb
    except ImportError:
        print("duckdb required: pip install duckdb", file=sys.stderr)
        raise SystemExit(2)

    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    written = []

    for table in TABLES:
        rnd = random.Random(f"{seed}:{table}")
        offset = rnd.randint(1000, 9000)

        # Clean, known-good partition.
        good = WAREHOUSE_DIR / f"{table}_good.parquet"
        con.execute(
            f"""
            COPY (
              SELECT
                i AS id,
                {offset} + i AS customer_id,
                'sku_' || (i % 500) AS sku,
                round(10 + (i * 7 % 900) / 10.0, 2) AS amount,
                current_date - 1 AS partition_date
              FROM range(1, {rows + 1}) t(i)
            ) TO '{good}' (FORMAT PARQUET)
            """
        )
        written.append(good.name)

    # Corrupted variants, one per failure mode per table. run_demo stages the one
    # a given incident needs into {table}_today.parquet, so every table can
    # exhibit either signature.
    for table in TABLES:
        rnd = random.Random(f"{seed}:{table}")
        offset = rnd.randint(1000, 9000)
        null_flood = WAREHOUSE_DIR / f"{table}_null_flood.parquet"
        con.execute(
            f"""
            COPY (
              SELECT
                i AS id,
                CASE WHEN (i * 37) % 100 < 40 THEN NULL ELSE {offset} + i END AS customer_id,
                'sku_' || (i % 500) AS sku,
                round(10 + (i * 7 % 900) / 10.0, 2) AS amount,
                current_date AS partition_date
              FROM range(1, {rows + 1}) t(i)
            ) TO '{null_flood}' (FORMAT PARQUET)
            """
        )
        written.append(null_flood.name)

    # Volume-collapsed variants, used when a table's incident is VOLUME_COLLAPSE.
    for table in TABLES:
        rnd = random.Random(f"{seed}:{table}")
        offset = rnd.randint(1000, 9000)
        collapsed = WAREHOUSE_DIR / f"{table}_collapsed.parquet"
        tiny = max(int(rows * 0.02), 5)
        con.execute(
            f"""
            COPY (
              SELECT
                i AS id,
                {offset} + i AS customer_id,
                'sku_' || (i % 500) AS sku,
                round(10 + (i * 7 % 900) / 10.0, 2) AS amount,
                current_date AS partition_date
              FROM range(1, {tiny + 1}) t(i)
            ) TO '{collapsed}' (FORMAT PARQUET)
            """
        )
        written.append(collapsed.name)

    return written


# --------------------------------------------------------------------------
# Tier 3 -- lineage graph and incident queue
# --------------------------------------------------------------------------


def write_lineage() -> int:
    now = datetime.now(timezone.utc)
    nodes: list[dict] = []
    edges: list[dict] = []

    change_meta = {
        "raw_customers_cdc": ("sc_001", 2, "renamed cust_id -> customer_id_v2 in CDC connector", "devon"),
        "raw_shipments_cdc": ("sc_002", 3, "carrier feed column remapped during connector upgrade", "ana"),
        "raw_inventory_snapshots": ("sc_003", 1, "snapshot export dropped cust_id alias", "devon"),
    }

    for table in TABLES:
        owner_name, handle, team = OWNERS[table]

        nodes.append({"id": table, "label": "Table", "name": table, "layer": "mart"})
        nodes.append({"id": f"job_{table}_daily", "label": "Job",
                      "name": f"job_{table}_daily", "schedule": "0 2 * * *"})
        nodes.append({"id": f"owner_{table}", "label": "Owner", "name": owner_name,
                      "slack_handle": handle, "team": team})

        edges.append({"from": f"job_{table}_daily", "to": table, "rel": "WRITES"})
        edges.append({"from": f"owner_{table}", "to": table, "rel": "OWNS"})
        edges.append({"from": f"owner_{table}", "to": f"job_{table}_daily", "rel": "OWNS"})

        # Staging layer, so the walk is genuinely multi-hop.
        staging = f"staging_{table}"
        nodes.append({"id": staging, "label": "Table", "name": staging, "layer": "staging"})
        nodes.append({"id": f"job_{staging}", "label": "Job", "name": f"job_{staging}",
                      "schedule": "0 1 * * *"})
        edges.append({"from": f"job_{table}_daily", "to": staging, "rel": "READS"})
        edges.append({"from": f"job_{staging}", "to": staging, "rel": "WRITES"})

        for raw in UPSTREAM[table]:
            if not any(n["id"] == raw for n in nodes):
                nodes.append({"id": raw, "label": "Table", "name": raw, "layer": "raw"})
            edges.append({"from": f"job_{staging}", "to": raw, "rel": "READS"})

            column_id = f"{raw}.customer_id"
            if not any(n["id"] == column_id for n in nodes):
                nodes.append({"id": column_id, "label": "Column", "name": "customer_id",
                              "dtype": "BIGINT", "nullable": False})
                edges.append({"from": raw, "to": column_id, "rel": "HAS_COLUMN"})

            if raw in change_meta:
                change_id, days_ago, description, author = change_meta[raw]
                if not any(n["id"] == change_id for n in nodes):
                    nodes.append({
                        "id": change_id,
                        "label": "SchemaChange",
                        "ts": (now - timedelta(days=days_ago)).isoformat(timespec="seconds"),
                        "description": description,
                        "author": author,
                    })
                edges.append({"from": change_id, "to": column_id, "rel": "ALTERED"})

            if not any(n["id"] == f"owner_{raw}" for n in nodes):
                nodes.append({"id": f"owner_{raw}", "label": "Owner", "name": "Devon Ilesanmi",
                              "slack_handle": "@devon", "team": "platform-ingest"})
                edges.append({"from": f"owner_{raw}", "to": raw, "rel": "OWNS"})

    LINEAGE_FILE.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=2))
    return len(nodes)


def write_incidents() -> list[dict]:
    """The order is the demo curve: two cold, four warm.

    Signature 1 (NULL_FLOOD) first fires on orders, then replays on shipments and
    inventory. Signature 2 (VOLUME_COLLAPSE) first fires on shipments, then
    replays on inventory and orders. Different tables, same signature -- which is
    exactly why signature() excludes the table name.
    """
    queue = [
        {"table": "orders", "corruption": "null_flood", "expect": "COLD"},
        {"table": "shipments", "corruption": "collapsed", "expect": "COLD"},
        {"table": "shipments", "corruption": "null_flood", "expect": "WARM"},
        {"table": "inventory", "corruption": "collapsed", "expect": "WARM"},
        {"table": "inventory", "corruption": "null_flood", "expect": "WARM"},
        {"table": "orders", "corruption": "collapsed", "expect": "WARM"},
    ]
    INCIDENTS_FILE.write_text(json.dumps(queue, indent=2))
    return queue


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate all data tiers")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rows", type=int, default=20000,
                        help="clean rows per table (diagnostics are relative, "
                             "so this does not need to be large)")
    args = parser.parse_args()

    random.seed(args.seed)
    ensure_dirs()

    docs = write_corpus()
    files = write_warehouse(args.rows, args.seed)
    nodes = write_lineage()
    queue = write_incidents()

    print(f"corpus      {docs} documents -> {CORPUS_DIR.relative_to(DATA_DIR.parent)}")
    print(f"warehouse   {len(files)} parquet files ({args.rows} rows/table)")
    print(f"lineage     {nodes} nodes -> {LINEAGE_FILE.name}")
    print(f"incidents   {len(queue)} queued -> {INCIDENTS_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
