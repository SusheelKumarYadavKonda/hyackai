# Compounding On-Call Agent

3-hour hackathon build, 2 developers. Ground: @docs/01_GROUND.md · Spec: @docs/02_SPEC.md

## My scope (Sush)
I own: core/, adapters/rote_adapter.py, adapters/rocketride_adapter.py,
scripts/run_demo.py, scripts/render_report.py, tests/

DO NOT edit: adapters/contracts.py (frozen), adapters/cognee_adapter.py,
adapters/hydra_adapter.py, adapters/hotdata_adapter.py, scripts/make_data.py,
scripts/seed_graph.py, data/ — another developer owns these.

## Rules
- contracts.py is FROZEN. If something doesn't fit it, tell me — don't edit it.
- Every adapter keeps a working stub path behind a config flag. Never break it.
- signature() must NOT include the table name (spec §4.1). A test asserts this.
- Never write Parquet-derived numbers into HydraDB.
- No new dependencies without asking. Snyk findings cost us points.
- Terminal output only. No web UI, no React.
- You do not know these five SDKs' real APIs. Never invent method names —
  ask me to paste the vendor docs.

## Commands
python scripts/run_demo.py      # all 6 incidents
pytest -q