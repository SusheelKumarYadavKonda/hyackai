"""Lineage graph and multi-hop traversal.

Spec §4.3 assumed HydraDB would serve OpenCypher. It does not -- HydraDB v2 has
no graph query language. So the lineage graph is ours: a small typed node/edge
store loaded from data/lineage.json, traversed in Python.

That keeps the *intent* of the 3-hop query (failing table -> writer job ->
upstream inputs -> recent schema changes -> owner) while removing a dependency
on a capability no vendor here actually offers.

Hard boundary: nothing derived from Parquet ever enters this graph. Row counts
and null rates are live truth from hotdata, stale within hours. Lineage and
ownership are stable for weeks. Mixing them collapses two layers into one.
"""

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from adapters.contracts import UpstreamChange


class LineageGraph:
    def __init__(self, nodes: list[dict], edges: list[dict]):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = edges
        self._out: dict[str, list[dict]] = {}
        self._in: dict[str, list[dict]] = {}
        for edge in edges:
            self._out.setdefault(edge["from"], []).append(edge)
            self._in.setdefault(edge["to"], []).append(edge)

    @classmethod
    def load(cls, path: Path) -> "LineageGraph":
        payload = json.loads(path.read_text())
        return cls(payload["nodes"], payload["edges"])

    def node_count(self) -> int:
        return len(self.nodes)

    # -- traversal helpers -------------------------------------------------

    def _neighbours(self, node_id: str, rel: str, reverse: bool = False) -> list[str]:
        index = self._in if reverse else self._out
        key = "from" if reverse else "to"
        return [e[key] for e in index.get(node_id, []) if e["rel"] == rel]

    def owner_of(self, node_id: str) -> Optional[str]:
        """Follow (Owner)-[:OWNS]->(node) backwards."""
        for owner_id in self._neighbours(node_id, "OWNS", reverse=True):
            owner = self.nodes.get(owner_id, {})
            return owner.get("slack_handle") or owner.get("name")
        return None

    def upstream_tables(self, table: str, max_hops: int = 3) -> list[tuple[str, int]]:
        """Breadth-first walk up the lineage: table <- job -> upstream tables.

        Returns (table_id, hops) pairs. Equivalent to the spec's
        MATCH (t)<-[:WRITES]-(j)-[:READS]->(up) pattern, generalised to N hops.
        """
        found: list[tuple[str, int]] = []
        seen = {table}
        queue: deque[tuple[str, int]] = deque([(table, 0)])

        while queue:
            current, hops = queue.popleft()
            if hops >= max_hops:
                continue
            for job_id in self._neighbours(current, "WRITES", reverse=True):
                for upstream in self._neighbours(job_id, "READS"):
                    if upstream in seen:
                        continue
                    seen.add(upstream)
                    found.append((upstream, hops + 1))
                    queue.append((upstream, hops + 1))

        return found

    def recent_changes(
        self, table: str, within_days: int = 7, max_hops: int = 3
    ) -> list[UpstreamChange]:
        """The query that earns its keep: what changed upstream, and who owns it.

        Walks up to `max_hops` upstream, finds SchemaChange nodes that ALTERED a
        column on those tables within the window, and attaches the owner.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
        results: list[UpstreamChange] = []

        for upstream, hops in self.upstream_tables(table, max_hops):
            for column_id in self._neighbours(upstream, "HAS_COLUMN"):
                for change_id in self._neighbours(column_id, "ALTERED", reverse=True):
                    change = self.nodes.get(change_id, {})
                    ts = change.get("ts")
                    if ts and _parse_ts(ts) < cutoff:
                        continue
                    results.append(
                        {
                            "upstream_table": self.nodes[upstream].get("name", upstream),
                            "changed_column": self.nodes[column_id].get("name", column_id),
                            "change": change.get("description", ""),
                            "author": change.get("author", ""),
                            "owner": self.owner_of(upstream) or "",
                            "hops": hops,
                        }
                    )

        results.sort(key=lambda r: (r["hops"], r["upstream_table"]))
        return results


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
