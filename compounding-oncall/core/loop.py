"""The core loop. One incident, eight steps.

    1 trigger        supervise a real job, catch a real non-zero exit
    2 signature      normalise stderr into a failure fingerprint
    3 muscle check   seen this shape before? -> replay, skip to 7
    4 recall         lineage traversal + Cognee corpus + HydraDB outcomes
    5 diagnose       hotdata, live, against the actual Parquet
    6 decide + act   RocketRide chains the actions
    7 verify         re-run the real job, expect exit 0
    8 learn          capture path, write outcome, write narrative

Step 3 is the entire product. Everything else is evidence that the replay was
worth trusting.
"""

import time
from dataclasses import dataclass
from typing import Optional

from adapters.contracts import Decision, Diagnostics, GraphContext, Incident
from core import supervisor
from core.lineage import LineageGraph
from core.metrics import MetricsRecorder, RunRecord
from core.muscle import MuscleMemoryStore
from core.signature import sig_type, signature


@dataclass
class Agent:
    """Wires the layers together. Constructed once, reused for every incident."""

    lineage: LineageGraph
    muscle: MuscleMemoryStore
    corpus: object       # CorpusMemory   (Cognee)
    outcomes: object     # OutcomeMemory  (HydraDB)
    engine: object       # QueryEngine    (hotdata)
    orchestrator: object # Orchestrator   (RocketRide)
    metrics: MetricsRecorder
    verbose: bool = True

    def _say(self, message: str) -> None:
        if self.verbose:
            print(message)

    # -- steps ------------------------------------------------------------

    async def recall(self, incident: Incident, sig: str) -> GraphContext:
        """Step 4. Three memory layers, three different questions."""
        upstream = self.lineage.recent_changes(incident["table"])
        corpus = await self.corpus.semantic_recall(incident["error_text"], k=3)
        outcomes = await self.outcomes.recall(incident["table"], sig)

        owner = self.lineage.owner_of(incident["table"])
        return {
            "upstream_tables": upstream,
            "past_fixes": outcomes.get("past_fixes", []),
            "corpus_recall": corpus,
            "owner": owner,
            "node_count": self.lineage.node_count() + outcomes.get("node_count", 0),
        }

    async def diagnose(self, incident: Incident, kind: str) -> Diagnostics:
        """Step 5. Live numbers. Never cached, never persisted."""
        return await self.engine.diagnose(incident["table"], kind)

    async def learn(
        self, incident: Incident, sig: str, decision: Decision, elapsed_ms: int
    ) -> None:
        """Step 8. Three writes, one per memory layer.

        Muscle memory gets the path (so the next identical failure replays).
        HydraDB gets the outcome (so the session accumulates).
        Cognee gets the narrative (so it stays load-bearing, not a one-time import).
        """
        self.muscle.capture(sig, incident["table"], decision)

        await self.outcomes.write_resolution(
            sig=sig,
            table=incident["table"],
            action=decision["action"],
            diagnosis=decision["diagnosis"],
            incident_id=incident["incident_id"],
            ms=elapsed_ms,
        )

        await self.corpus.add_resolution(
            f"Incident {incident['incident_id']} on {incident['table']}: "
            f"{decision['diagnosis']} Resolved by {decision['action']} in {elapsed_ms}ms."
        )

    # -- the loop ---------------------------------------------------------

    async def run_incident(self, table: str, run_no: int) -> Optional[RunRecord]:
        started = time.perf_counter()

        # 1 trigger
        self._say(f"\n[{run_no}] {table}: running job_{table}_daily ...")
        incident = await supervisor.watch(table)
        if incident is None:
            self._say(f"[{run_no}] {table}: job succeeded, nothing to do")
            return None

        first_line = incident["error_text"].splitlines()[0]
        self._say(f"[{run_no}] {table}: exit {incident['exit_code']}")
        self._say(f"      {first_line[:120]}")

        # 2 signature
        sig = signature(incident["error_text"], table)
        kind = sig_type(incident["error_text"])
        self._say(f"[{run_no}] {table}: signature {sig} ({kind})")

        # 3 muscle memory
        remembered = self.muscle.lookup(sig)

        if remembered:
            path = "WARM"
            self._say(
                f"[{run_no}] {table}: HIT -- replaying path captured from "
                f"{remembered['captured_from_table']}, no reasoning"
            )
            decision = self.muscle.replay(sig, table)
            diagnostics: Diagnostics = {
                "metrics": {},
                "failed_check": None,
                "detail": "skipped (replayed)",
            }
        else:
            path = "COLD"
            self._say(f"[{run_no}] {table}: MISS -- reasoning from scratch")

            # 4 recall
            ctx = await self.recall(incident, sig)
            hops = len(ctx["upstream_tables"])
            self._say(
                f"      lineage: {hops} recent upstream change(s), "
                f"corpus: {len(ctx['corpus_recall'])} doc(s), "
                f"prior fixes: {len(ctx['past_fixes'])}"
            )

            # 5 diagnose
            diagnostics = await self.diagnose(incident, kind)
            self._say(f"      diagnostics: {diagnostics['detail']}")

            # 6 decide + act
            decision = await self.orchestrator.decide_and_act(incident, ctx, diagnostics)
            rationale = decision["params"].get("rationale", "")
            self._say(f"      action: {decision['action']} ({rationale})")
            self._say(f"      chain:  {' -> '.join(decision['chain'])}")

        # 7 verify -- the real job, again
        passed, exit_code, detail = await supervisor.verify(table)
        self._say(f"[{run_no}] {table}: verify exit {exit_code} ({'PASS' if passed else 'FAIL'})")

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # 8 learn (only on a verified cold resolution)
        if passed and path == "COLD":
            await self.learn(incident, sig, decision, elapsed_ms)

        return self.metrics.record(
            run_no=run_no,
            signature=sig,
            table=table,
            sig_type=kind,
            path=path,
            elapsed_ms=elapsed_ms,
            tokens=decision["tokens_used"],
            memory_nodes=await self.outcomes.count() + self.lineage.node_count(),
            replay_paths=self.muscle.count(),
            resolved=passed,
            action=decision["action"],
            diagnosis=decision["diagnosis"],
            failed_check=diagnostics["failed_check"],
            verify_detail=detail,
        )
