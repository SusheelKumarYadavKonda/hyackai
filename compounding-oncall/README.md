# Compounding On-Call Agent

An incident responder for data pipelines that stops re-diagnosing the same
failure. When a job crashes, the agent fingerprints the failure. If it has seen
that shape before it replays the previously captured resolution deterministically
— no reasoning, no tokens. If it hasn't, it does the expensive work once, then
remembers.

Six incidents, two failure modes, three tables. The submission is the curve.

```
run  table      signature          type             path       ms  tokens     cost   mem  paths  ok
  1  orders     sig_ba4a7a717fd1   NULL_FLOOD       COLD      861    1768  $0.0080    37      1  PASS
  2  shipments  sig_9c3d1a8ea10f   VOLUME_COLLAPSE  COLD      845    1757  $0.0079    38      2  PASS
  3  shipments  sig_ba4a7a717fd1   NULL_FLOOD       WARM      152       0  $0.0000    38      2  PASS
  4  inventory  sig_9c3d1a8ea10f   VOLUME_COLLAPSE  WARM      158       0  $0.0000    38      2  PASS
  5  inventory  sig_ba4a7a717fd1   NULL_FLOOD       WARM      120       0  $0.0000    38      2  PASS
  6  orders     sig_9c3d1a8ea10f   VOLUME_COLLAPSE  WARM      107       0  $0.0000    38      2  PASS

  cold path (reasoned)   2 runs, avg 853ms, avg 1762 tokens
  warm path (replayed)   4 runs, avg 134ms, avg 0 tokens
  speedup                6.4x faster on replay
```

Run 3 is the point. It replays a path first captured on `orders` and applies it
to `shipments` — a different table, same failure mode. The signature deliberately
excludes the table name, which is what makes the transfer possible.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/make_data.py --seed 42
.venv/bin/python scripts/run_demo.py
.venv/bin/python scripts/render_report.py    # -> report.html
```

Runs with no credentials: every layer defaults to a working stub.

```bash
.venv/bin/python -m pytest -q      # 15 tests
.venv/bin/python scripts/smoke.py  # PASS/FAIL per layer
```

## The loop

```
scripts/run_job.py --table orders          real subprocess, real assertions
   fails NOT NULL on customer_id  ->  stderr, exit 1
        |
        v
supervisor catches returncode != 0, captures stderr verbatim
        |
        v
signature(error_text)                      timestamps, paths, counts, table name stripped
        |
        v
   muscle memory: seen this signature?
   |-- HIT  -> replay captured path, 0 tokens, skip to verify
   |-- MISS -> lineage    walk up to 3 hops, find recent upstream schema changes
               cognee     what do our runbooks and postmortems say?
               hydradb    what have we resolved earlier this session?
               hotdata    what is wrong with the data right now?
               rocketride decide -> quarantine -> rerun -> notify
        |
        v
verify: re-run the real job, expect exit 0
        |
        v
learn: muscle captures the path
       hydradb records the outcome
       cognee gets the resolution narrative
```

The trigger and the verify are both real processes. `PASS` means a subprocess
exited 0, not that the agent asserted success.

## Layers

| Layer | Stores | The question only it answers |
|---|---|---|
| **muscle memory** (in-house) | executable paths | "Have we solved this exact shape before?" |
| **Cognee** | prose → graph | "What did the org already know?" |
| **HydraDB** | outcomes | "What have *we* learned this session?" |
| **hotdata.dev** | nothing (computes live) | "What is wrong with the data right now?" |
| **RocketRide** | — | "What do we do, and do it" |

Three memory layers, three different time horizons: Cognee is what was known
before we started, HydraDB is what accumulated since, muscle memory is the
replayable path. That distinction is the architecture defence.

### Why muscle memory is in-house

The spec called for Rote (Modiqo) here. It has no published API surface, and it
is the single load-bearing claim of the project — so it is implemented directly
(`core/muscle.py`, ~90 lines, JSON on disk) rather than delegated to an SDK we
cannot verify. A hit is a dictionary lookup: no network, no embeddings, no
model. That is why replay costs zero tokens rather than fewer tokens.

### The boundary that must not blur

Nothing derived from Parquet is ever written to a memory layer. Null rates and
row counts are live truth from hotdata, stale within hours. Lineage and ownership
are stable for weeks. A stale copy in memory is worse than no copy.

## Integrating the real services

Every layer flips independently via `.env`, so you can bring one up without
touching the others and fall back instantly if a vendor misbehaves.

```bash
cp .env.example .env
```

Verified API surfaces are pinned in [`docs/03_SDK_NOTES.md`](docs/03_SDK_NOTES.md).
Read that before wiring anything — three of the four vendors do not work the way
the original spec assumed, and the notes record what is actually true.

### hotdata.dev — start here

Highest value per minute: it makes the diagnostics real numbers from a real
engine. The stub already runs the same SQL locally through DuckDB, so only the
execution engine changes.

```bash
pip install hotdata
brew install hotdata-dev/tap/cli
hotdata auth login && hotdata auth status
```

Set `HOTDATA_API_KEY`, `HOTDATA_WORKSPACE_ID`, then `USE_STUB_HOTDATA=0`.
Uploads cost wall-clock; `--rows 20000` is deliberate, since every diagnostic is
relative and absolute volume proves nothing extra.

### HydraDB — the accumulator

```bash
pip install "hydradb-sdk>=2,<3"
```

Set `HYDRA_DB_API_KEY`, then `USE_STUB_HYDRA=0`. Note that HydraDB v2 has **no
Cypher and no typed edges** — the lineage graph is ours (`core/lineage.py`),
traversed in Python. HydraDB stores resolutions as `type="memory"` scoped to a
`collection`, and both database creation and ingestion are async and polled.

### Cognee — the corpus

```bash
pip install cognee
```

Set `OPENAI_API_KEY` (Cognee needs an LLM for extraction), then
`USE_STUB_COGNEE=0`. `cognify()` calls a model per document, so ingesting the
corpus takes minutes — start it early and let it run in the background.

### RocketRide — the orchestrator

```bash
pip install rocketride
```

Author a pipeline in the VS Code extension, then set `ROCKETRIDE_APIKEY` and
`USE_STUB_ROCKETRIDE=0`. **Use `https://` or `wss://` only** — `http://`, `ws://`,
and a bare `host:port` silently downgrade to an unencrypted connection, and the
adapter refuses to start rather than connect in plaintext.

## Layout

```
adapters/
  contracts.py           FROZEN — async protocols for every layer
  cognee_adapter.py       corpus recall     (stub: keyword overlap)
  hydra_adapter.py        outcome memory    (stub: in-process)
  hotdata_adapter.py      live diagnostics  (stub: local DuckDB)
  rocketride_adapter.py   decide + act      (stub: same reasoning, no network)
core/
  signature.py           normalise stderr -> stable fingerprint
  muscle.py              capture + deterministic replay
  lineage.py             typed node/edge store, N-hop traversal
  supervisor.py          run the job, catch the crash, verify the fix
  loop.py                run_incident() — the eight steps
  metrics.py             RunRecord + the curve
  config.py              .env, per-adapter stub flags
scripts/
  make_data.py           all three data tiers, seeded
  run_job.py             the job that actually fails
  run_demo.py            all six incidents
  render_report.py       metrics -> report.html
  smoke.py               PASS/FAIL per layer
tests/                   15 tests; the first one protects the curve
docs/03_SDK_NOTES.md     verified vendor API surfaces
```

## What is stubbed

Whatever `scripts/smoke.py` says. The layer status line prints at the top of
every demo run and in the report footer, because saying plainly what is stubbed
is part of the pitch — a stubbed layer explained cleanly beats a
half-integrated one that crashes on stage.

Token counts on stubbed layers are estimated from real context size (serialised
error text, corpus excerpts, upstream changes, diagnostics) and labelled as
estimates. With RocketRide live they are replaced by its traced counts.
