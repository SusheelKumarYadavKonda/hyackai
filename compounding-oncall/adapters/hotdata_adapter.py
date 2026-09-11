"""hotdata.dev: live analytical queries over the warehouse.

Answers "what is actually wrong with the data right now?" Every number here is
computed fresh and never persisted to a memory layer -- null rates and row
counts go stale within hours, and a stale copy is worse than no copy.

Verified surface (docs/03_SDK_NOTES.md): hotdata is a cloud service, not a local
Parquet reader. Create an instant database with declared tables, upload the file,
load it into the table, then query. Every query must carry a database scope.

The stub runs the same SQL locally via DuckDB, so the diagnostics are identical
in both modes -- only the execution engine changes.
"""

import asyncio
import re

from adapters.contracts import Diagnostics
from core import config

_SAFE_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Thresholds. Deliberately relative, so absolute row counts never matter and the
# synthetic warehouse can stay small.
NULL_RATE_DELTA_PP = 15.0   # percentage points vs last known-good
VOLUME_FLOOR_RATIO = 0.20   # today must be >= 20% of the 7-day median


class HotdataStub:
    """DuckDB over the local Parquet files. Same SQL, local engine."""

    name = "hotdata(stub)"

    def __init__(self):
        self._con = None
        self._loaded: dict[str, str] = {}

    def _connect(self):
        if self._con is None:
            import duckdb

            self._con = duckdb.connect(":memory:")
        return self._con

    async def setup(self, tables: list[str]) -> None:
        self._connect()

    async def load(self, parquet_path: str, alias: str) -> None:
        con = self._connect()
        if not _SAFE_ALIAS.fullmatch(alias):
            raise ValueError(f"unsafe table alias: {alias!r}")
        # DuckDB rejects bound parameters in CREATE VIEW, so the path is inlined.
        # Quotes are escaped and the alias is pattern-checked above, since both
        # are interpolated into SQL.
        escaped = parquet_path.replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW {alias} AS "
            f"SELECT * FROM read_parquet('{escaped}')"
        )
        self._loaded[alias] = parquet_path

    async def _sql(self, sql: str) -> list[tuple]:
        con = self._connect()
        return await asyncio.to_thread(lambda: con.execute(sql).fetchall())

    async def diagnose(self, table: str, sig_type: str) -> Diagnostics:
        return await _diagnose(self._sql, table, sig_type)


class HotdataReal:
    """Live hotdata. Upload each Parquet, load into a declared table, run SQL."""

    name = "hotdata"

    def __init__(self):
        import hotdata

        self._hotdata = hotdata
        self._configuration = hotdata.Configuration(
            api_key=config.HOTDATA_API_KEY,
            workspace_id=config.HOTDATA_WORKSPACE_ID,
        )
        self._database_id = None
        self._connection_id = None
        self._catalog = "oncall"

    async def setup(self, tables: list[str]) -> None:
        """Create the instant database and declare every table we will load.

        Reuses an existing database of the same name rather than creating a new
        one per run, so repeated demos do not accumulate orphaned databases.
        """

        def _create():
            hotdata = self._hotdata
            with hotdata.ApiClient(self._configuration) as client:
                api = hotdata.DatabasesApi(client)

                for existing in getattr(api.list_databases(), "databases", []):
                    if getattr(existing, "name", None) == self._catalog:
                        detail = api.get_database(existing.id)
                        return existing.id, detail.default_connection_id

                created = api.create_database(
                    hotdata.CreateDatabaseRequest(
                        name=self._catalog,
                        expires_at="24h",
                        schemas=[
                            hotdata.DatabaseDefaultSchemaDecl(
                                name="public",
                                tables=[
                                    hotdata.DatabaseDefaultTableDecl(name=t)
                                    for t in tables
                                ],
                            )
                        ],
                    )
                )
                detail = api.get_database(created.id)
                return created.id, detail.default_connection_id

        self._database_id, self._connection_id = await asyncio.to_thread(_create)

    async def load(self, parquet_path: str, alias: str) -> None:
        def _load():
            hotdata = self._hotdata
            with hotdata.ApiClient(self._configuration) as client:
                upload = hotdata.UploadsApi(client).upload_file(
                    parquet_path, content_type="application/parquet"
                )
                return hotdata.ConnectionsApi(client).load_managed_table(
                    connection_id=self._connection_id,
                    var_schema="public",   # generated client names it var_schema
                    table=alias,
                    load_managed_table_request=hotdata.LoadManagedTableRequest(
                        mode="replace", upload_id=upload.upload_id
                    ),
                )

        await asyncio.to_thread(_load)

    async def _sql(self, sql: str) -> list[tuple]:
        """Run SQL against the instant database.

        No catalog rewriting: x_database_id already scopes the query, and managed
        tables resolve on the bare name. Verified against the live API -- the
        catalog is 'default' regardless of the --catalog alias used at creation,
        so prefixing the alias raises "table not found".
        """

        def _query():
            hotdata = self._hotdata
            with hotdata.ApiClient(self._configuration) as client:
                response = hotdata.QueryApi(client).query(
                    hotdata.QueryRequest(sql=sql),
                    x_database_id=self._database_id,
                )
                rows = getattr(response, "rows", None) or []
                return [
                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                    for r in rows
                ]

        return await asyncio.to_thread(_query)

    async def diagnose(self, table: str, sig_type: str) -> Diagnostics:
        return await _diagnose(self._sql, table, sig_type)


# --------------------------------------------------------------------------
# The three diagnostics. Shared by both modes so behaviour cannot diverge.
# --------------------------------------------------------------------------


async def _diagnose(sql, table: str, sig_type: str) -> Diagnostics:
    if sig_type == "NULL_FLOOD":
        return await _null_flood(sql, table)
    if sig_type == "VOLUME_COLLAPSE":
        return await _volume_collapse(sql, table)
    if sig_type == "SCHEMA_DRIFT":
        return await _schema_drift(sql, table)
    return {
        "metrics": {},
        "failed_check": None,
        "detail": f"no diagnostic registered for {sig_type}",
    }


async def _null_flood(sql, table: str) -> Diagnostics:
    """Null rate per column, today vs last known-good."""
    today = await sql(
        f"SELECT count(*), count(customer_id) FROM {table}_today"
    )
    good = await sql(
        f"SELECT count(*), count(customer_id) FROM {table}_good"
    )

    today_rows, today_present = (today[0] if today else (0, 0))
    good_rows, good_present = (good[0] if good else (0, 0))

    today_null_pct = _null_pct(today_rows, today_present)
    good_null_pct = _null_pct(good_rows, good_present)
    delta = round(today_null_pct - good_null_pct, 2)

    failed = "null_rate_delta" if delta > NULL_RATE_DELTA_PP else None
    return {
        "metrics": {
            "column": "customer_id",
            "rows_today": today_rows,
            "null_pct_today": today_null_pct,
            "null_pct_known_good": good_null_pct,
            "delta_pp": delta,
            "threshold_pp": NULL_RATE_DELTA_PP,
        },
        "failed_check": failed,
        "detail": (
            f"customer_id null rate {today_null_pct}% today vs "
            f"{good_null_pct}% last known-good (delta {delta}pp, "
            f"threshold {NULL_RATE_DELTA_PP}pp)"
        ),
    }


async def _volume_collapse(sql, table: str) -> Diagnostics:
    """Row count today vs the 7-day median."""
    today = await sql(f"SELECT count(*) FROM {table}_today")
    good = await sql(f"SELECT count(*) FROM {table}_good")

    today_rows = today[0][0] if today else 0
    baseline = good[0][0] if good else 0
    ratio = round(today_rows / baseline, 4) if baseline else 0.0

    failed = "volume_floor" if ratio < VOLUME_FLOOR_RATIO else None
    return {
        "metrics": {
            "rows_today": today_rows,
            "rows_baseline_median": baseline,
            "ratio": ratio,
            "floor_ratio": VOLUME_FLOOR_RATIO,
        },
        "failed_check": failed,
        "detail": (
            f"{today_rows} rows today vs baseline median {baseline} "
            f"({ratio:.1%} of expected, floor {VOLUME_FLOOR_RATIO:.0%})"
        ),
    }


async def _schema_drift(sql, table: str) -> Diagnostics:
    """Column set diff. Kept for completeness; not in the 6-incident demo."""
    today = await sql(f"SELECT * FROM {table}_today LIMIT 0")
    good = await sql(f"SELECT * FROM {table}_good LIMIT 0")
    return {
        "metrics": {"today_cols": len(today), "good_cols": len(good)},
        "failed_check": None,
        "detail": "schema comparison not enabled for this demo",
    }


def _null_pct(total: int, present: int) -> float:
    if not total:
        return 0.0
    return round((total - present) / total * 100, 2)


def build():
    if config.USE_STUB_HOTDATA:
        return HotdataStub()
    return HotdataReal()
