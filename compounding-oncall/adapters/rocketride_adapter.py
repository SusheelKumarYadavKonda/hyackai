"""RocketRide: decide what to do, then chain the actions that do it.

Answers "what do we do about it, and do it." Takes graph context plus live
diagnostics and produces a diagnosis and an action chain.

Verified surface (docs/03_SDK_NOTES.md): RocketRide is a pipeline runtime, not a
tool-call API. use(filepath|pipeline) -> token, send(token, payload) -> result,
terminate(token). asyncio-first, built on websockets.

Security note: the URI scheme selects transport. https:// and wss:// give an
encrypted connection; http://, ws://, or a bare host:port silently downgrade to
plaintext. We reject the downgrade rather than connecting insecurely.
"""

import asyncio
import json
import time

from adapters.contracts import Decision, Diagnostics, GraphContext, Incident
from core import config

# The action chain. Three steps, so orchestration is visible rather than implied.
CHAIN = ["quarantine_partition", "rerun_job", "notify_owner"]


def _reason(incident: Incident, ctx: GraphContext, diag: Diagnostics) -> tuple[str, str, dict]:
    """Form a diagnosis from graph context + live diagnostics.

    Shared by both modes: the stub and the real path must reach the same
    conclusion, or the demo's warm/cold comparison is measuring the wrong thing.
    """
    table = incident["table"]
    upstream = ctx["upstream_tables"][0] if ctx["upstream_tables"] else None

    if upstream:
        cause = (
            f"{upstream['change']} on {upstream['upstream_table']}."
            f"{upstream['changed_column']} "
            f"({upstream['hops']} hop(s) upstream, changed by {upstream['author']})"
        )
        owner = upstream["owner"]
    else:
        cause = "no recent upstream schema change found in the lineage window"
        owner = ctx.get("owner") or "unassigned"

    diagnosis = f"{table} failed: {diag['detail']}. Probable cause: {cause}."

    if diag["failed_check"] == "null_rate_delta":
        action = "quarantine_partition"
    elif diag["failed_check"] == "volume_floor":
        action = "quarantine_partition"
    else:
        action = "open_ticket"

    params = {
        "table": table,
        "partition": incident["run_ts"][:10],
        "notify": owner,
        "job_id": incident["job_id"],
    }
    return diagnosis, action, params


def _estimate_tokens(incident: Incident, ctx: GraphContext, diag: Diagnostics) -> int:
    """Token estimate for the cold path.

    Sized from the actual context we would send to a model: error text, recalled
    corpus excerpts, upstream changes, and diagnostic numbers. Labelled as an
    estimate in the report -- with a real LLM behind RocketRide this is replaced
    by the traced count.
    """
    payload = json.dumps(
        {
            "error": incident["error_text"],
            "corpus": ctx["corpus_recall"],
            "upstream": ctx["upstream_tables"],
            "fixes": ctx["past_fixes"],
            "diagnostics": diag["metrics"],
        }
    )
    prompt_tokens = len(payload) // 4
    return prompt_tokens + 1400  # system prompt + reasoning + structured output


class RocketRideStub:
    name = "rocketride(stub)"

    async def decide_and_act(self, incident, ctx, diag) -> Decision:
        diagnosis, action, params = _reason(incident, ctx, diag)

        # Deliberate latency so the cold path costs visible wall-clock, the way
        # real reasoning does. Named, not hidden.
        await asyncio.sleep(0.35)

        chain = []
        for step in CHAIN:
            await asyncio.sleep(0.12)
            chain.append(step)

        return {
            "diagnosis": diagnosis,
            "action": action,
            "params": params,
            "chain": chain,
            "tokens_used": _estimate_tokens(incident, ctx, diag),
        }


class RocketRideReal:
    name = "rocketride"

    def __init__(self, pipeline_path: str | None = None):
        from rocketride import RocketRideClient

        self._client_cls = RocketRideClient
        self._pipeline_path = pipeline_path
        self._uri = config.ROCKETRIDE_URI

        if not self._uri.startswith(("https://", "wss://")):
            raise ValueError(
                f"refusing insecure RocketRide URI {self._uri!r}: http://, ws://, "
                "and bare host:port silently downgrade to an unencrypted "
                "connection. Use https:// or wss://."
            )

    async def decide_and_act(self, incident, ctx, diag) -> Decision:
        diagnosis, action, params = _reason(incident, ctx, diag)
        started = time.perf_counter()
        chain: list[str] = []
        tokens = 0

        payload = json.dumps(
            {
                "incident": incident,
                "diagnosis": diagnosis,
                "proposed_action": action,
                "params": params,
                "chain": CHAIN,
            }
        )

        try:
            async with self._client_cls(
                uri=self._uri, auth=config.ROCKETRIDE_APIKEY
            ) as client:
                result = await client.use(filepath=self._pipeline_path)
                token = result["token"]
                try:
                    for step in CHAIN:
                        out = await client.send(
                            token,
                            payload,
                            objinfo={"name": f"{step}.json"},
                            mimetype="application/json",
                        )
                        chain.append(step)
                        tokens += _tokens_from(out)
                finally:
                    await client.terminate(token)
        except Exception as exc:  # noqa: BLE001
            # Never let the orchestrator take the incident down: fall back to the
            # decision we already formed and say so in the chain.
            chain.append(f"rocketride_unavailable({type(exc).__name__})")

        if not tokens:
            tokens = _estimate_tokens(incident, ctx, diag)

        _ = time.perf_counter() - started
        return {
            "diagnosis": diagnosis,
            "action": action,
            "params": params,
            "chain": chain,
            "tokens_used": tokens,
        }


def _tokens_from(result) -> int:
    """RocketRide traces tokens per call. Read them when present."""
    if isinstance(result, dict):
        for key in ("tokens", "total_tokens", "tokens_used"):
            if key in result:
                try:
                    return int(result[key])
                except (TypeError, ValueError):
                    return 0
    return 0


def build(pipeline_path: str | None = None):
    if config.USE_STUB_ROCKETRIDE:
        return RocketRideStub()
    return RocketRideReal(pipeline_path)
