"""Muscle memory: capture successful resolution paths, replay them exactly.

This is the load-bearing claim of the project, so it is deliberately in-house
rather than delegated to an SDK. A JSON file on disk, keyed by failure
signature. No LLM, no network, no embeddings -- a hit is a dictionary lookup.

The asymmetry is the whole point. A miss costs seconds of reasoning and
thousands of tokens. A hit costs a file read and zero tokens.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from adapters.contracts import Decision, ReplayPath


class MuscleMemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self._paths: dict[str, ReplayPath] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._paths = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                # A corrupt store must never take the demo down. Start clean.
                self._paths = {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._paths, indent=2))

    # -- protocol ---------------------------------------------------------

    def lookup(self, sig: str) -> Optional[ReplayPath]:
        return self._paths.get(sig)

    def capture(self, sig: str, table: str, decision: Decision) -> None:
        """Record a verified resolution. First writer wins: the captured path is
        the one that was proven to work, so later incidents replay it rather
        than overwriting it."""
        if sig in self._paths:
            return
        self._paths[sig] = {
            "signature": sig,
            "diagnosis": decision["diagnosis"],
            "action": decision["action"],
            "params": decision["params"],
            "chain": decision["chain"],
            "captured_from_table": table,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "replay_count": 0,
        }
        self._flush()

    def replay(self, sig: str, table: str) -> Decision:
        """Rebuild the decision from the captured path, retargeted at this table.

        No reasoning happens here. The diagnosis text and action come straight
        off disk; only the table parameter changes.
        """
        path = self._paths.get(sig)
        if path is None:
            raise KeyError(f"no captured path for {sig}")

        path["replay_count"] += 1
        self._flush()

        params = dict(path["params"])
        params["table"] = table

        return {
            "diagnosis": path["diagnosis"],
            "action": path["action"],
            "params": params,
            "chain": path["chain"],
            "tokens_used": 0,
        }

    def count(self) -> int:
        return len(self._paths)

    # -- helpers ----------------------------------------------------------

    def reset(self) -> None:
        """Clear the store so a demo starts cold. Called by run_demo --reset."""
        self._paths = {}
        if self.path.exists():
            self.path.unlink()
