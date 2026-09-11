#!/usr/bin/env python3
"""PASS/FAIL connectivity check for every layer.

Run this the morning of the event. Anything red gets fixed before the clock
starts -- a credential problem discovered at 1:40 is unrecoverable.

Usage:
    python scripts/smoke.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {detail}")


async def main() -> int:
    print("Compounding On-Call -- smoke test")
    print(f"layers: {config.stub_summary()}\n")

    # -- local prerequisites ---------------------------------------------
    try:
        import duckdb  # noqa: F401
        check("duckdb", True, "import ok")
    except ImportError as exc:
        check("duckdb", False, str(exc))

    check("corpus", config.CORPUS_DIR.exists() and any(config.CORPUS_DIR.iterdir()),
          f"{len(list(config.CORPUS_DIR.glob('*'))) if config.CORPUS_DIR.exists() else 0} files")
    check("lineage", config.LINEAGE_FILE.exists(), config.LINEAGE_FILE.name)
    check("incidents", config.INCIDENTS_FILE.exists(), config.INCIDENTS_FILE.name)

    parquet = list(config.WAREHOUSE_DIR.glob("*.parquet")) if config.WAREHOUSE_DIR.exists() else []
    check("warehouse", bool(parquet), f"{len(parquet)} parquet files")

    # -- the real job has to actually fail -------------------------------
    try:
        from core import supervisor

        result = await supervisor.run_job("orders", "good")
        check("job (known-good)", result.exit_code == 0, f"exit {result.exit_code}")
    except Exception as exc:  # noqa: BLE001
        check("job (known-good)", False, f"{type(exc).__name__}: {exc}")

    # -- signature invariant ---------------------------------------------
    try:
        from core.signature import signature

        a = signature("job_orders_daily FAILED: NOT NULL constraint violated on "
                      "column customer_id in orders: 800 nulls", "orders")
        b = signature("job_shipments_daily FAILED: NOT NULL constraint violated on "
                      "column customer_id in shipments: 912 nulls", "shipments")
        check("signature invariant", a == b, "same mode across tables" if a == b
              else "MISMATCH -- replay will never fire")
    except Exception as exc:  # noqa: BLE001
        check("signature invariant", False, str(exc))

    # -- vendor layers ---------------------------------------------------
    await _check_cognee()
    await _check_hydra()
    await _check_hotdata()
    await _check_rocketride()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print(f"all {len(RESULTS)} checks passed")
    return 0


async def _check_cognee() -> None:
    if config.USE_STUB_COGNEE:
        check("cognee", True, "stub mode")
        return

    cloud = bool(config.COGNEE_API_KEY and config.COGNEE_BASE_URL)
    if not cloud and not config.OPENAI_API_KEY:
        check("cognee", False,
              "set COGNEE_API_KEY + COGNEE_BASE_URL (cloud) or OPENAI_API_KEY (local)")
        return

    try:
        from adapters import cognee_adapter

        adapter = cognee_adapter.build(config.CORPUS_DIR)
        await adapter._ensure_served()
        check("cognee", True, "cloud tenant attached" if cloud else "local mode")
    except Exception as exc:  # noqa: BLE001
        check("cognee", False, f"{type(exc).__name__}: {exc}")


async def _check_hydra() -> None:
    if config.USE_STUB_HYDRA:
        check("hydradb", True, "stub mode")
        return
    if not config.HYDRA_DB_API_KEY:
        check("hydradb", False, "HYDRA_DB_API_KEY not set")
        return
    try:
        from adapters import hydra_adapter

        adapter = hydra_adapter.build()
        await adapter.setup()
        check("hydradb", True, f"database '{config.HYDRA_DATABASE}' ready")
    except Exception as exc:  # noqa: BLE001
        check("hydradb", False, f"{type(exc).__name__}: {exc}")


async def _check_hotdata() -> None:
    if config.USE_STUB_HOTDATA:
        check("hotdata", True, "stub mode (duckdb)")
        return
    if not (config.HOTDATA_API_KEY and config.HOTDATA_WORKSPACE_ID):
        check("hotdata", False, "HOTDATA_API_KEY / HOTDATA_WORKSPACE_ID not set")
        return
    try:
        from adapters import hotdata_adapter

        adapter = hotdata_adapter.build()
        await adapter.setup(["orders_today"])
        check("hotdata", True, "instant database created")
    except Exception as exc:  # noqa: BLE001
        check("hotdata", False, f"{type(exc).__name__}: {exc}")


async def _check_rocketride() -> None:
    if config.USE_STUB_ROCKETRIDE:
        check("rocketride", True, "stub mode")
        return
    if not config.ROCKETRIDE_APIKEY:
        check("rocketride", False, "ROCKETRIDE_APIKEY not set")
        return
    try:
        from adapters import rocketride_adapter

        adapter = rocketride_adapter.build()
        check("rocketride", True, f"client ready ({config.ROCKETRIDE_URI})")
    except Exception as exc:  # noqa: BLE001
        check("rocketride", False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
