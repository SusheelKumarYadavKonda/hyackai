#!/usr/bin/env python3
"""A pipeline job that actually fails.

This is the trigger. The agent does not read incidents off a queue and pretend a
job broke -- it supervises a real subprocess, and reacts to a real non-zero exit
with real stderr. That also makes step 7 (verify) honest: after the fix, this
same command either exits 0 or it does not.

Usage:
    python scripts/run_job.py --table orders                 # reads today's partition
    python scripts/run_job.py --table orders --partition good # reads known-good

Exit codes:
    0  assertions passed
    1  data quality assertion failed (the incident)
    2  operational failure (missing file, unreadable parquet)
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import WAREHOUSE_DIR  # noqa: E402

NOT_NULL_COLUMNS = {"orders": "customer_id", "shipments": "customer_id", "inventory": "customer_id"}
MIN_ROWS = 1000


def _read(path: Path):
    try:
        import duckdb
    except ImportError:
        print("duckdb is required to run jobs: pip install duckdb", file=sys.stderr)
        raise SystemExit(2)

    con = duckdb.connect(":memory:")
    total, present = con.execute(
        "SELECT count(*), count(customer_id) FROM read_parquet(?)", [str(path)]
    ).fetchone()
    return total, present


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a data pipeline job")
    parser.add_argument("--table", required=True)
    parser.add_argument("--partition", default="today", choices=["today", "good"])
    args = parser.parse_args()

    path = WAREHOUSE_DIR / f"{args.table}_{args.partition}.parquet"
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not path.exists():
        print(f"[{run_ts}] job_{args.table}_daily: input not found: {path}", file=sys.stderr)
        return 2

    print(f"[{run_ts}] job_{args.table}_daily: reading {path.name}")
    total, present = _read(path)
    nulls = total - present
    column = NOT_NULL_COLUMNS[args.table]

    # Volume assertion.
    if total < MIN_ROWS:
        print(
            f"[{run_ts}] job_{args.table}_daily FAILED: row count check on "
            f"{args.table}: got {total} rows, expected at least {MIN_ROWS}. "
            f"Partition volume collapsed. source='{path.name}'",
            file=sys.stderr,
        )
        return 1

    # Null assertion.
    if nulls:
        pct = nulls / total * 100
        print(
            f"[{run_ts}] job_{args.table}_daily FAILED: NOT NULL constraint "
            f"violated on column {column} in {args.table}: {nulls} null values "
            f"across {total} rows ({pct:.1f}%). First offending row 4471. "
            f"source='{path.name}'",
            file=sys.stderr,
        )
        return 1

    print(f"[{run_ts}] job_{args.table}_daily: OK ({total} rows, 0 nulls in {column})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
