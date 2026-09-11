"""Presentation for the live demo.

Terminal only, by design -- a UI would cost curve time. The job here is to make
the eight steps legible to a room: each incident renders as a panel with the
crash, the fingerprint, the memory decision, and the verify, so an audience can
follow the cold-vs-warm difference without reading log lines.

Colour is ANSI and degrades to plain text when piped or when NO_COLOR is set.
"""

import os
import sys

_ENABLED = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str) -> str:
    return code if _ENABLED else ""


DIM = _c("\033[2m")
BOLD = _c("\033[1m")
RESET = _c("\033[0m")
RED = _c("\033[31m")
GREEN = _c("\033[32m")
YELLOW = _c("\033[33m")
BLUE = _c("\033[34m")
CYAN = _c("\033[36m")
MAGENTA = _c("\033[35m")

WIDTH = 74


def rule(char: str = "─") -> str:
    return DIM + char * WIDTH + RESET


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(f"{BOLD}{title}{RESET}")
    if subtitle:
        print(f"{DIM}{subtitle}{RESET}")
    print(rule("━"))


def incident_header(run_no: int, table: str, job_id: str) -> None:
    print()
    print(rule("━"))
    print(f"{BOLD}INCIDENT {run_no}{RESET}  {DIM}·{RESET}  {job_id}  "
          f"{DIM}·{RESET}  table {BOLD}{table}{RESET}")
    print(rule("━"))


def step(number: int, label: str, detail: str = "") -> None:
    """One numbered step of the loop."""
    marker = f"{DIM}{number}{RESET}"
    print(f" {marker} {BOLD}{label}{RESET}" + (f"  {detail}" if detail else ""))


def sub(text: str, colour: str = "") -> None:
    print(f"   {colour}{text}{RESET}" if colour else f"   {DIM}{text}{RESET}")


def crash(exit_code: int, first_line: str) -> None:
    step(1, "PIPELINE CRASHED", f"{RED}exit {exit_code}{RESET}")
    sub(_truncate(first_line, WIDTH - 5), RED)


def fingerprint(sig: str, kind: str) -> None:
    step(2, "FINGERPRINT", f"{CYAN}{sig}{RESET} {DIM}({kind}){RESET}")


def memory_hit(source_table: str) -> None:
    step(3, "MUSCLE MEMORY", f"{GREEN}HIT{RESET}")
    sub(f"seen this shape before on '{source_table}' -- replaying, no reasoning", GREEN)


def memory_miss() -> None:
    step(3, "MUSCLE MEMORY", f"{YELLOW}MISS{RESET}")
    sub("never seen this failure shape -- reasoning from scratch", YELLOW)


def recall(hops: int, corpus: int, fixes: int, owner: str) -> None:
    step(4, "RECALL", f"{DIM}what do we already know?{RESET}")
    sub(f"lineage    {hops} recent upstream change(s), owner {owner}")
    sub(f"cognee     {corpus} runbook/postmortem match(es)")
    sub(f"hydradb    {fixes} prior resolution(s)")


def diagnose(detail: str, failed_check: str | None) -> None:
    step(5, "DIAGNOSE", f"{DIM}what is wrong right now?{RESET}")
    sub(_truncate(detail, WIDTH - 5), MAGENTA)
    if failed_check:
        sub(f"failed check: {failed_check}", MAGENTA)


def act(action: str, rationale: str, chain: list[str]) -> None:
    step(6, "DECIDE + ACT", f"{BOLD}{action}{RESET}")
    sub(f"because: {rationale}", BLUE)
    sub("chain:   " + f" {DIM}→{RESET} ".join(chain))


def verify(passed: bool, exit_code: int) -> None:
    colour = GREEN if passed else RED
    verdict = "RESOLVED" if passed else "STILL FAILING"
    step(7, "VERIFY", f"{colour}job re-run, exit {exit_code} · {verdict}{RESET}")


def learn(items: list[str]) -> None:
    step(8, "LEARN", f"{DIM}so the next one is free{RESET}")
    for item in items:
        sub(item, GREEN)


def outcome(path: str, elapsed_ms: int, tokens: int) -> None:
    colour = YELLOW if path == "COLD" else GREEN
    print(rule())
    print(f" {colour}{BOLD}{path}{RESET}  "
          f"{BOLD}{elapsed_ms:,}ms{RESET}  {DIM}·{RESET}  "
          f"{BOLD}{tokens:,}{RESET} tokens")


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def curve(summary: dict) -> None:
    """The closing slide: the claim, in numbers."""
    banner("THE COMPOUNDING CURVE", "the same agent, getting faster as it remembers")

    cold_ms = summary["avg_cold_ms"]
    warm_ms = summary["avg_warm_ms"]
    scale = max(cold_ms, 1)

    for label, ms, tokens, count, colour in (
        ("COLD  (reasoned)", cold_ms, summary["avg_cold_tokens"],
         summary["cold_runs"], YELLOW),
        ("WARM  (replayed)", warm_ms, summary["avg_warm_tokens"],
         summary["warm_runs"], GREEN),
    ):
        bar_len = max(int(ms / scale * 42), 1)
        print(f" {label}  {colour}{'█' * bar_len}{RESET}")
        print(f"   {DIM}{count} run(s) · avg {ms:,}ms · avg {tokens:,} tokens{RESET}")

    print()
    if summary["speedup"]:
        print(f" {BOLD}{summary['speedup']}× faster{RESET} on replay, "
              f"at {BOLD}zero{RESET} token cost")
    print(f" {summary['resolved']}/{summary['runs']} incidents resolved and verified "
          f"by re-running the real job")
    print(f" {DIM}spend ${summary['total_cost_usd']:.4f} "
          f"(${summary['cost_avoided_usd']:.4f} avoided by replay){RESET}")
