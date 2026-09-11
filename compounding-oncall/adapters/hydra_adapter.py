"""HydraDB: durable, accumulating record of what this agent has resolved.

Answers "what have *we* learned since the session started?" -- as distinct from
Cognee, which holds what the org knew beforehand. Different time horizons.

Verified surface (docs/03_SDK_NOTES.md): no Cypher, no labeled nodes or typed
edges. Retrieval is hybrid semantic + BM25 over chunks, with knowledge/memory
type split and collection scoping. Both database creation and ingestion are
async and must be polled. Every response is a {success, data, error, meta}
envelope -- read payloads from .data.

Resolutions are written as type="memory" so they accumulate and are retrievable
mid-run. Parquet-derived numbers are never written here.
"""

import asyncio
import json
from datetime import datetime, timezone

from core import config


class HydraStub:
    name = "hydradb(stub)"

    def __init__(self):
        self._memories: list[dict] = []

    async def setup(self) -> None:
        return None

    async def recall(self, table: str, sig: str) -> dict:
        fixes = [
            {
                "action": m["action"],
                "description": m["diagnosis"],
                "success_count": m["success_count"],
                "first_seen_table": m["table"],
            }
            for m in self._memories
            if m["sig"] == sig
        ]
        fixes.sort(key=lambda f: -f["success_count"])
        return {"past_fixes": fixes[:3], "node_count": len(self._memories)}

    async def write_resolution(
        self, sig, table, action, diagnosis, incident_id, ms
    ) -> None:
        for m in self._memories:
            if m["sig"] == sig and m["action"] == action:
                m["success_count"] += 1
                return
        self._memories.append(
            {
                "sig": sig,
                "table": table,
                "action": action,
                "diagnosis": diagnosis,
                "incident_id": incident_id,
                "resolution_ms": ms,
                "success_count": 1,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def count(self) -> int:
        return len(self._memories)


class HydraReal:
    name = "hydradb"

    def __init__(self):
        # The package is `hydra_db` and the kwarg is `token`. The dashboard
        # snippet showing `from hydradb import HydraDB` with `api_key=` does not
        # match the published SDK; verified against the installed package.
        from hydra_db import AsyncHydraDB

        self._client = AsyncHydraDB(token=config.HYDRA_DB_API_KEY)
        self._database = config.HYDRA_DATABASE
        self._collection = config.HYDRA_COLLECTION
        self._written = 0

    # -- lifecycle --------------------------------------------------------

    async def setup(self) -> None:
        """Create the database if needed, then poll until it accepts data.

        Querying before infra is ready returns 422 TENANT_INFRA_NOT_READY, so
        the poll is mandatory rather than defensive.
        """
        try:
            await self._call(self._client.databases.create, database=self._database)
        except Exception as exc:  # noqa: BLE001 - 409 on re-run is expected
            if "ALREADY_EXISTS" not in str(exc).upper():
                raise

        for _ in range(60):
            status = await self._call(
                self._client.databases.status, database=self._database
            )
            if _dig(status, "data", "infra", "ready_for_ingestion"):
                return
            await asyncio.sleep(5)
        raise TimeoutError("HydraDB infra not ready")

    # -- reads ------------------------------------------------------------

    async def recall(self, table: str, sig: str) -> dict:
        result = await self._call(
            self._client.query,
            database=self._database,
            collection=self._collection,
            query=f"Past resolutions for failure signature {sig} on table {table}",
            type="memory",
            query_by="hybrid",
            mode="fast",
            max_results=5,
        )
        chunks = _dig(result, "data", "chunks") or []
        fixes = []
        for chunk in chunks:
            meta = _get(chunk, "additional_metadata") or {}
            if _get(meta, "sig") and _get(meta, "sig") != sig:
                continue
            fixes.append(
                {
                    "action": _get(meta, "action") or "unknown",
                    "description": (_get(chunk, "chunk_content") or "")[:200],
                    "success_count": int(_get(meta, "success_count") or 1),
                    "first_seen_table": _get(meta, "table") or "",
                }
            )
        return {"past_fixes": fixes[:3], "node_count": await self.count()}

    async def count(self) -> int:
        """Accumulated memories, read from the live database.

        Verified shape: data.memory_collection.row_count (a
        TenantsTenantStatsResponse, alongside knowledge_collection). This is the
        number that visibly grows across the demo run.
        """
        try:
            stats = await self._call(
                self._client.databases.stats, database=self._database
            )
            count = _dig(stats, "data", "memory_collection", "row_count")
            if count is not None:
                # Take the larger of the server count and what we have written.
                # Indexing lags a write by a few seconds, so reporting the raw
                # server figure mid-run would show the count going backwards.
                return max(int(count), self._written)
        except Exception:  # noqa: BLE001 - stats is nice-to-have, never fatal
            pass
        return self._written

    # -- writes -----------------------------------------------------------

    async def write_resolution(
        self, sig, table, action, diagnosis, incident_id, ms
    ) -> None:
        """Write the outcome as a memory. `memories` must be a JSON string.

        Indexing is async: a freshly written memory takes a few seconds to reach
        `graph_creation` before it is searchable. We deliberately do not block
        the loop waiting for it -- the incident is already resolved, and muscle
        memory (not HydraDB) is what makes the next identical failure fast.
        HydraDB is the durable record, so eventual visibility is the right
        trade for keeping incident latency honest.
        """
        memory = {
            "id": f"incident_{incident_id}",
            "title": f"{sig} resolved on {table}",
            "text": (
                f"Incident {incident_id}: failure signature {sig} on table {table} "
                f"was resolved by action '{action}'. Diagnosis: {diagnosis} "
                f"Resolution took {ms}ms."
            ),
            "infer": False,
            "additional_metadata": {
                "sig": sig,
                "table": table,
                "action": action,
                "incident_id": incident_id,
                "resolution_ms": ms,
                "success_count": 1,
            },
        }
        await self._call(
            self._client.context.ingest,
            type="memory",
            database=self._database,
            collection=self._collection,
            memories=json.dumps([memory]),
        )
        self._written += 1

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    async def _call(fn, **kwargs):
        """AsyncHydraDB returns coroutines, but tolerate a sync client too."""
        result = fn(**kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result


def _get(obj, key):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _dig(obj, *keys):
    for key in keys:
        obj = _get(obj, key)
        if obj is None:
            return None
    return obj


def build():
    if config.USE_STUB_HYDRA:
        return HydraStub()
    return HydraReal()
