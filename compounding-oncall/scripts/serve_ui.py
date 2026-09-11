#!/usr/bin/env python3
"""Serve the live UI.

    python scripts/serve_ui.py            # http://127.0.0.1:8420
    python scripts/serve_ui.py --port 9000

Streams the agent's own events to the browser over server-sent events, so the
page shows the actual run rather than a replay. Stdlib only -- no web framework,
because every added dependency is Snyk scan surface.

Binds to localhost only. This is a demo surface with no authentication, so it
must not be exposed on a public interface.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402
from core.events import BUS  # noqa: E402

UI_DIR = config.REPO_ROOT / "ui"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

_run_lock = asyncio.Lock()


# --------------------------------------------------------------------------
# The demo run, driven by the UI's Run button
# --------------------------------------------------------------------------


async def run_incidents() -> None:
    """Execute the six incidents, emitting events as the agent works."""
    from adapters import cognee_adapter, hotdata_adapter, hydra_adapter, rocketride_adapter
    from core.lineage import LineageGraph
    from core.loop import Agent
    from core.metrics import MetricsRecorder
    from core.muscle import MuscleMemoryStore
    from scripts.run_demo import stage

    BUS.reset()
    BUS.emit(
        "layers",
        layers={
            "cognee": "STUB" if config.USE_STUB_COGNEE else "REAL",
            "hydradb": "STUB" if config.USE_STUB_HYDRA else "REAL",
            "hotdata": "STUB" if config.USE_STUB_HOTDATA else "REAL",
            "rocketride": "STUB" if config.USE_STUB_ROCKETRIDE else "REAL",
            "muscle": "IN-HOUSE",
        },
    )

    queue = json.loads(config.INCIDENTS_FILE.read_text())
    lineage = LineageGraph.load(config.LINEAGE_FILE)

    muscle = MuscleMemoryStore(config.MUSCLE_FILE)
    muscle.reset()

    corpus = cognee_adapter.build(config.CORPUS_DIR)
    outcomes = hydra_adapter.build()
    engine = hotdata_adapter.build()
    orchestrator = rocketride_adapter.build()
    metrics = MetricsRecorder()

    ingested = await corpus.ingest(str(config.CORPUS_DIR))
    BUS.emit("ingested", documents=ingested["documents"], mode=ingested["mode"])

    await outcomes.setup()
    cleared = await outcomes.reset()
    BUS.emit(
        "reset",
        cleared=cleared,
        note=f"Cleared {cleared} prior resolution(s) so incident 1 is genuinely cold.",
    )

    await engine.setup([f"{t}_{s}" for t in config.TABLES for s in ("today", "good")])
    for table in config.TABLES:
        for suffix in ("today", "good"):
            path = config.WAREHOUSE_DIR / f"{table}_{suffix}.parquet"
            if path.exists():
                await engine.load(str(path), f"{table}_{suffix}")

    agent = Agent(
        lineage=lineage,
        muscle=muscle,
        corpus=corpus,
        outcomes=outcomes,
        engine=engine,
        orchestrator=orchestrator,
        metrics=metrics,
        verbose=False,
    )

    for run_no, item in enumerate(queue, start=1):
        stage(item["table"], item["corruption"])
        await engine.load(
            str(config.WAREHOUSE_DIR / f"{item['table']}_today.parquet"),
            f"{item['table']}_today",
        )
        await agent.run_incident(item["table"], run_no)

    close = getattr(corpus, "close", None)
    if close:
        try:
            await close()
        except Exception:  # noqa: BLE001
            pass

    summary = metrics.summary()
    metrics.save(config.METRICS_FILE)
    BUS.emit("done", layer_line=config.stub_summary(), **summary)


# --------------------------------------------------------------------------
# HTTP, hand-rolled on asyncio streams
# --------------------------------------------------------------------------


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not request_line:
            return
        try:
            method, target, _ = request_line.decode("latin-1").split()
        except ValueError:
            return

        while True:  # drain headers
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if line in (b"\r\n", b"\n", b""):
                break

        path = target.split("?", 1)[0]

        if path == "/events":
            await serve_events(writer)
        elif path == "/start" and method == "POST":
            await serve_start(writer)
        elif path in ("/", "/index.html"):
            await serve_file(writer, UI_DIR / "index.html")
        elif path.startswith("/static/"):
            await serve_static(writer, path)
        else:
            await respond(writer, 404, "text/plain; charset=utf-8", b"not found")
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def serve_static(writer: asyncio.StreamWriter, path: str) -> None:
    name = Path(path).name  # basename only: no traversal outside ui/
    target = UI_DIR / name
    if not target.is_file() or target.parent != UI_DIR:
        await respond(writer, 404, "text/plain; charset=utf-8", b"not found")
        return
    await serve_file(writer, target)


async def serve_file(writer: asyncio.StreamWriter, path: Path) -> None:
    if not path.is_file():
        await respond(writer, 404, "text/plain; charset=utf-8", b"not found")
        return
    ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    await respond(writer, 200, ctype, path.read_bytes())


async def serve_start(writer: asyncio.StreamWriter) -> None:
    if _run_lock.locked():
        await respond(writer, 409, "application/json", b'{"error":"already running"}')
        return

    async def guarded() -> None:
        async with _run_lock:
            try:
                await run_incidents()
            except Exception as exc:  # noqa: BLE001
                BUS.emit("error", message=f"{type(exc).__name__}: {exc}")
                print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    asyncio.create_task(guarded())
    await respond(writer, 202, "application/json", b'{"status":"started"}')


async def serve_events(writer: asyncio.StreamWriter) -> None:
    """Server-sent events. One connection per browser tab."""
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"
        b"Cache-Control: no-cache\r\n"
        b"Connection: keep-alive\r\n"
        b"X-Content-Type-Options: nosniff\r\n\r\n"
    )
    await writer.drain()

    queue = BUS.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                writer.write(b": keep-alive\n\n")  # stop proxies idling us out
                await writer.drain()
                continue
            if event is None:
                break
            writer.write(f"data: {event.to_json()}\n\n".encode())
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        BUS.unsubscribe(queue)


async def respond(
    writer: asyncio.StreamWriter, status: int, ctype: str, body: bytes
) -> None:
    reason = {200: "OK", 202: "Accepted", 404: "Not Found", 409: "Conflict"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"X-Content-Type-Options: nosniff\r\n"
        f"Connection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the live incident UI")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    if not config.INCIDENTS_FILE.exists():
        print("no data. run: python scripts/make_data.py --seed 42", file=sys.stderr)
        return 2

    # Localhost only: the UI has no authentication.
    server = await asyncio.start_server(handle, "127.0.0.1", args.port)
    print(f"Compounding On-Call UI  →  http://127.0.0.1:{args.port}")
    print(f"layers: {config.stub_summary()}")
    print("open the page, then press Run incidents.  ctrl-c to stop.")
    async with server:
        await server.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nstopped")
