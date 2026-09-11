"""Metrics recorder. This is the submission.

Built before any real integration, because the compounding curve -- cold runs
slow and expensive, warm runs fast and free -- is what we are actually claiming.
Everything else is evidence for this table.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from core.config import COST_PER_1K_TOKENS


@dataclass
class RunRecord:
    run_no: int
    signature: str
    table: str
    sig_type: str
    path: str  # "COLD" | "WARM"
    elapsed_ms: int
    tokens: int
    cost_usd: float
    memory_nodes: int
    replay_paths: int
    resolved: bool
    action: str = ""
    diagnosis: str = ""
    failed_check: Optional[str] = None
    verify_detail: str = ""


@dataclass
class MetricsRecorder:
    records: list[RunRecord] = field(default_factory=list)

    def record(self, **kwargs) -> RunRecord:
        kwargs.setdefault("cost_usd", round(kwargs.get("tokens", 0) / 1000 * COST_PER_1K_TOKENS, 6))
        record = RunRecord(**kwargs)
        self.records.append(record)
        return record

    # -- terminal output --------------------------------------------------

    HEADER = (
        f"{'run':>3}  {'table':<10} {'signature':<18} {'type':<16} "
        f"{'path':<5} {'ms':>7} {'tokens':>7} {'cost':>9} {'mem':>5} {'paths':>6}  ok"
    )

    @staticmethod
    def format_row(r: RunRecord) -> str:
        return (
            f"{r.run_no:>3}  {r.table:<10} {r.signature:<18} {r.sig_type:<16} "
            f"{r.path:<5} {r.elapsed_ms:>7} {r.tokens:>7} "
            f"${r.cost_usd:>8.4f} {r.memory_nodes:>5} {r.replay_paths:>6}  "
            f"{'PASS' if r.resolved else 'FAIL'}"
        )

    def print_header(self) -> None:
        print(self.HEADER)
        print("-" * len(self.HEADER))

    def print_row(self, record: RunRecord) -> None:
        print(self.format_row(record))

    def print_table(self) -> None:
        self.print_header()
        for record in self.records:
            self.print_row(record)

    # -- the claim --------------------------------------------------------

    def summary(self) -> dict:
        cold = [r for r in self.records if r.path == "COLD"]
        warm = [r for r in self.records if r.path == "WARM"]

        def mean(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        cold_ms = mean([r.elapsed_ms for r in cold])
        warm_ms = mean([r.elapsed_ms for r in warm])
        cold_tokens = mean([r.tokens for r in cold])

        return {
            "runs": len(self.records),
            "cold_runs": len(cold),
            "warm_runs": len(warm),
            "resolved": sum(1 for r in self.records if r.resolved),
            "avg_cold_ms": round(cold_ms),
            "avg_warm_ms": round(warm_ms),
            "speedup": round(cold_ms / warm_ms, 1) if warm_ms else 0.0,
            "avg_cold_tokens": round(cold_tokens),
            "avg_warm_tokens": round(mean([r.tokens for r in warm])),
            "total_cost_usd": round(sum(r.cost_usd for r in self.records), 6),
            "cost_avoided_usd": round(
                len(warm) * cold_tokens / 1000 * COST_PER_1K_TOKENS, 6
            ),
        }

    def print_summary(self) -> None:
        s = self.summary()
        print()
        print("Compounding curve")
        print("-" * 46)
        print(f"  incidents resolved     {s['resolved']}/{s['runs']}")
        print(f"  cold path (reasoned)   {s['cold_runs']} runs, avg {s['avg_cold_ms']}ms, "
              f"avg {s['avg_cold_tokens']} tokens")
        print(f"  warm path (replayed)   {s['warm_runs']} runs, avg {s['avg_warm_ms']}ms, "
              f"avg {s['avg_warm_tokens']} tokens")
        if s["speedup"]:
            print(f"  speedup                {s['speedup']}x faster on replay")
        print(f"  spend                  ${s['total_cost_usd']:.4f} "
              f"(${s['cost_avoided_usd']:.4f} avoided by replay)")

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "records": [asdict(r) for r in self.records],
                    "summary": self.summary(),
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: Path) -> "MetricsRecorder":
        recorder = cls()
        if path.exists():
            payload = json.loads(path.read_text())
            recorder.records = [RunRecord(**r) for r in payload.get("records", [])]
        return recorder
