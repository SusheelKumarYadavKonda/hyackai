#!/usr/bin/env python3
"""Run all six incidents and print the compounding curve.

Usage:
    python scripts/run_demo.py              # full run, cold start
    python scripts/run_demo.py --keep       # keep muscle memory (all warm)
    python scripts/run_demo.py --quiet      # table only, no per-step narration
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import cognee_adapter, hotdata_adapter, hydra_adapter, rocketride_adapter  # noqa: E402
from core import config  # noqa: E402
from core.lineage import LineageGraph  # noqa: E402
from core.loop import Agent  # noqa: E402
from core.metrics import MetricsRecorder  # noqa: E402
from core.muscle import MuscleMemoryStore  # noqa: E402


def stage(table: str, corruption: str) -> None:
    """Put the requested corrupted partition in place as today's data.

    The job always reads {table}_today.parquet, so staging is how we choose which
    failure mode this incident exhibits. The job itself knows nothing about it.
    """
    source = config.WAREHOUSE_DIR / f"{table}_{corruption}.parquet"
    target = config.WAREHOUSE_DIR / f"{table}_today.parquet"
    if not source.exists():
        raise FileNotFoundError(f"{source} missing -- run scripts/make_data.py first")
    if source != target:
        shutil.copyfile(source, target)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the incident demo")
    parser.add_argument("--keep", action="store_true",
                        help="keep existing muscle memory instead of starting cold")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config.ensure_dirs()

    if not config.INCIDENTS_FILE.exists():
        print("no data found. run: python scripts/make_data.py --seed 42", file=sys.stderr)
        return 2

    queue = json.loads(config.INCIDENTS_FILE.read_text())
    lineage = LineageGraph.load(config.LINEAGE_FILE)

    muscle = MuscleMemoryStore(config.MUSCLE_FILE)
    if not args.keep:
        muscle.reset()

    corpus = cognee_adapter.build(config.CORPUS_DIR)
    outcomes = hydra_adapter.build()
    engine = hotdata_adapter.build()
    orchestrator = rocketride_adapter.build()
    metrics = MetricsRecorder()

    print("Compounding On-Call Agent")
    print(f"layers: {config.stub_summary()}")
    print()

    # Ingest phase. Cognee reads the corpus; hotdata gets the warehouse loaded.
    ingested = await corpus.ingest(str(config.CORPUS_DIR))
    print(f"cognee: ingested {ingested['documents']} documents ({ingested['mode']})")

    await outcomes.setup()

    await engine.setup([f"{t}_{suffix}" for t in config.TABLES for suffix in ("today", "good")])
    for table in config.TABLES:
        for suffix in ("today", "good"):
            path = config.WAREHOUSE_DIR / f"{table}_{suffix}.parquet"
            if path.exists():
                await engine.load(str(path), f"{table}_{suffix}")
    print(f"hotdata: warehouse loaded ({len(config.TABLES)} tables)")
    print(f"lineage: {lineage.node_count()} nodes")

    agent = Agent(
        lineage=lineage,
        muscle=muscle,
        corpus=corpus,
        outcomes=outcomes,
        engine=engine,
        orchestrator=orchestrator,
        metrics=metrics,
        verbose=not args.quiet,
    )

    for run_no, item in enumerate(queue, start=1):
        stage(item["table"], item["corruption"])
        # hotdata holds its own copy, so refresh the staged partition there too.
        await engine.load(
            str(config.WAREHOUSE_DIR / f"{item['table']}_today.parquet"),
            f"{item['table']}_today",
        )
        await agent.run_incident(item["table"], run_no)

    print()
    metrics.print_table()
    metrics.print_summary()
    metrics.save(config.METRICS_FILE)
    print(f"\nmetrics written to {config.METRICS_FILE.relative_to(config.REPO_ROOT)}")

    unresolved = [r for r in metrics.records if not r.resolved]
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
