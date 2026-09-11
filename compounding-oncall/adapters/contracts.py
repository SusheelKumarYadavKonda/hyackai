"""Interface contracts for every external layer.

FROZEN. Do not edit without both developers agreeing in person.

These are *our* interfaces. Vendors adapt to them, not the reverse -- which is
what lets every layer keep a working stub behind a config flag.

All protocols are async: RocketRide is asyncio/websockets and Cognee is async
throughout, so a sync core would mean asyncio.run() wrappers in every adapter.
"""

from typing import Optional, Protocol, TypedDict, runtime_checkable

# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------


class Incident(TypedDict):
    """A pipeline failure, as captured from a real non-zero job exit."""

    incident_id: str
    job_id: str
    table: str
    run_ts: str
    error_text: str
    exit_code: int
    parquet_path: str


class UpstreamChange(TypedDict):
    """One recent change on a table upstream of the failing one."""

    upstream_table: str
    changed_column: str
    change: str
    author: str
    owner: str
    hops: int


class GraphContext(TypedDict):
    """What memory knows about this failure, before we look at any data.

    Assembled from the lineage graph (ours), Cognee (org prose), and HydraDB
    (outcomes this agent recorded earlier in the session).
    """

    upstream_tables: list[UpstreamChange]
    past_fixes: list[dict]
    corpus_recall: list[dict]
    owner: Optional[str]
    node_count: int


class Diagnostics(TypedDict):
    """Live truth about the data right now. Never persisted to a memory layer."""

    metrics: dict
    failed_check: Optional[str]
    detail: str


class Decision(TypedDict):
    """What to do, and the cost of working it out."""

    diagnosis: str
    action: str  # quarantine_partition | rerun_job | open_ticket
    params: dict
    chain: list[str]
    tokens_used: int


class Verification(TypedDict):
    """Did the fix hold? Answered by re-running the real job."""

    passed: bool
    exit_code: int
    detail: str


class ReplayPath(TypedDict):
    """A captured resolution, replayable without any reasoning."""

    signature: str
    diagnosis: str
    action: str
    params: dict
    chain: list[str]
    captured_from_table: str
    captured_at: str
    replay_count: int


# --------------------------------------------------------------------------
# Layer protocols
# --------------------------------------------------------------------------


@runtime_checkable
class CorpusMemory(Protocol):
    """Cognee. Unstructured org knowledge -> graph. What we knew beforehand."""

    async def ingest(self, corpus_dir: str) -> dict: ...

    async def semantic_recall(self, error_text: str, k: int = 3) -> list[dict]: ...

    async def add_resolution(self, text: str) -> None: ...


@runtime_checkable
class OutcomeMemory(Protocol):
    """HydraDB. Durable, accumulating record of what this agent has resolved."""

    async def setup(self) -> None: ...

    async def recall(self, table: str, sig: str) -> dict: ...

    async def write_resolution(
        self,
        sig: str,
        table: str,
        action: str,
        diagnosis: str,
        incident_id: str,
        ms: int,
    ) -> None: ...

    async def count(self) -> int: ...


@runtime_checkable
class QueryEngine(Protocol):
    """hotdata.dev. Live analytical queries over the warehouse."""

    async def setup(self, tables: list[str]) -> None: ...

    async def load(self, parquet_path: str, alias: str) -> None: ...

    async def diagnose(self, table: str, sig_type: str) -> Diagnostics: ...


@runtime_checkable
class Orchestrator(Protocol):
    """RocketRide. Turns context + diagnostics into a decision, then acts."""

    async def decide_and_act(
        self, incident: Incident, ctx: GraphContext, diag: Diagnostics
    ) -> Decision: ...


@runtime_checkable
class MuscleMemory(Protocol):
    """Ours. Capture successful paths, replay them deterministically.

    Deliberately in-house: this is the load-bearing claim of the project, so it
    cannot depend on an SDK we have no verified surface for.
    """

    def lookup(self, sig: str) -> Optional[ReplayPath]: ...

    def capture(self, sig: str, table: str, decision: Decision) -> None: ...

    def replay(self, sig: str, table: str) -> Decision: ...

    def count(self) -> int: ...
