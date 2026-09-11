#!/usr/bin/env python3
"""Render the metrics into a single static HTML file.

No framework, no build step, no assets. One file you can open or hand to a judge.

Usage:
    python scripts/render_report.py
"""

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402
from core.metrics import MetricsRecorder  # noqa: E402

CSS = """
:root { --ink:#16181d; --muted:#5b6472; --line:#e3e6ea; --cold:#b4441f; --warm:#1f7a4d; }
* { box-sizing:border-box; }
body { margin:0; padding:48px 24px; background:#fbfbfc; color:var(--ink);
       font:15px/1.6 ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif; }
main { max-width:920px; margin:0 auto; }
h1 { font-size:26px; margin:0 0 6px; letter-spacing:-0.01em; }
.sub { color:var(--muted); margin:0 0 32px; }
.cards { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:36px; }
.card { flex:1 1 170px; border:1px solid var(--line); border-radius:10px;
        padding:16px 18px; background:#fff; }
.card .k { font-size:12px; text-transform:uppercase; letter-spacing:0.06em;
           color:var(--muted); margin-bottom:6px; }
.card .v { font-size:26px; font-weight:600; letter-spacing:-0.02em; }
table { width:100%; border-collapse:collapse; background:#fff;
        border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th,td { padding:10px 12px; text-align:left; border-bottom:1px solid var(--line);
        font-variant-numeric:tabular-nums; }
th { font-size:12px; text-transform:uppercase; letter-spacing:0.05em;
     color:var(--muted); font-weight:600; background:#f7f8f9; }
tr:last-child td { border-bottom:none; }
td.num { text-align:right; }
.badge { display:inline-block; padding:2px 8px; border-radius:20px;
         font-size:12px; font-weight:600; }
.badge.cold { background:#fdeee8; color:var(--cold); }
.badge.warm { background:#e7f5ee; color:var(--warm); }
.bar { height:6px; border-radius:3px; background:#eceef1; overflow:hidden; }
.bar > i { display:block; height:100%; border-radius:3px; }
h2 { font-size:15px; text-transform:uppercase; letter-spacing:0.06em;
     color:var(--muted); margin:40px 0 12px; }
.note { border-left:3px solid var(--line); padding:2px 0 2px 14px;
        color:var(--muted); font-size:14px; margin:10px 0; }
code { background:#f2f3f5; padding:1px 5px; border-radius:4px; font-size:13px; }
footer { margin-top:40px; padding-top:16px; border-top:1px solid var(--line);
         color:var(--muted); font-size:13px; }
"""


def render(recorder: MetricsRecorder) -> str:
    s = recorder.summary()
    slowest = max((r.elapsed_ms for r in recorder.records), default=1) or 1

    rows = []
    for r in recorder.records:
        width = max(r.elapsed_ms / slowest * 100, 1.5)
        colour = "#b4441f" if r.path == "COLD" else "#1f7a4d"
        rows.append(f"""
      <tr>
        <td class="num">{r.run_no}</td>
        <td>{html.escape(r.table)}</td>
        <td><code>{html.escape(r.signature)}</code></td>
        <td>{html.escape(r.sig_type)}</td>
        <td><span class="badge {r.path.lower()}">{r.path}</span></td>
        <td class="num">{r.elapsed_ms:,}</td>
        <td style="width:110px"><div class="bar"><i style="width:{width:.1f}%;
            background:{colour}"></i></div></td>
        <td class="num">{r.tokens:,}</td>
        <td class="num">${r.cost_usd:.4f}</td>
        <td class="num">{r.memory_nodes}</td>
        <td>{'PASS' if r.resolved else 'FAIL'}</td>
      </tr>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Compounding On-Call Agent — run report</title>
<style>{CSS}</style></head>
<body><main>
  <h1>Compounding On-Call Agent</h1>
  <p class="sub">Six pipeline incidents, end to end. Cold runs reason from
     scratch; warm runs replay a previously captured resolution path.</p>

  <div class="cards">
    <div class="card"><div class="k">Resolved</div>
      <div class="v">{s['resolved']}/{s['runs']}</div></div>
    <div class="card"><div class="k">Avg cold</div>
      <div class="v">{s['avg_cold_ms']:,}<span style="font-size:14px"> ms</span></div></div>
    <div class="card"><div class="k">Avg warm</div>
      <div class="v">{s['avg_warm_ms']:,}<span style="font-size:14px"> ms</span></div></div>
    <div class="card"><div class="k">Speedup</div>
      <div class="v">{s['speedup']}&times;</div></div>
    <div class="card"><div class="k">Tokens avoided</div>
      <div class="v">{s['warm_runs'] * s['avg_cold_tokens']:,}</div></div>
  </div>

  <table>
    <thead><tr>
      <th>#</th><th>Table</th><th>Signature</th><th>Failure mode</th><th>Path</th>
      <th class="num">ms</th><th></th><th class="num">Tokens</th>
      <th class="num">Cost</th><th class="num">Memory</th><th>Verify</th>
    </tr></thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>

  <h2>How to read this</h2>
  <p class="note">Runs 1 and 2 are cold: each meets a failure signature it has
     never seen, so it walks the lineage graph, recalls the corpus, runs live
     diagnostics against the Parquet, and reasons out an action.</p>
  <p class="note">Runs 3 through 6 are warm. Each matched a signature already in
     muscle memory and replayed the captured path with no reasoning and zero
     tokens. Note that run 3 replays a path first captured on
     <code>orders</code> but applies it to <code>shipments</code> — the
     signature deliberately excludes the table name, so the same failure mode
     transfers across tables.</p>
  <p class="note">Every run ends by re-executing the real job as a subprocess.
     PASS means the process exited 0, not that the agent asserted success.</p>

  <h2>Layers</h2>
  <p class="note"><strong>Muscle memory</strong> (in-house) stores executable
     paths — exact signature match, deterministic replay.
     <strong>Cognee</strong> stores prose: runbooks and postmortems, what the org
     knew beforehand. <strong>HydraDB</strong> stores outcomes and accumulates
     during the session. <strong>hotdata.dev</strong> computes live truth about
     the data and is never cached. <strong>RocketRide</strong> chains the
     actions.</p>

  <footer>
    Layers this run: <code>{html.escape(config.stub_summary())}</code><br>
    Total spend ${s['total_cost_usd']:.4f} · approximately
    ${s['cost_avoided_usd']:.4f} avoided by replay. Token counts on stubbed
    layers are estimated from real context size and labelled as estimates.
  </footer>
</main></body></html>
"""


def main() -> int:
    recorder = MetricsRecorder.load(config.METRICS_FILE)
    if not recorder.records:
        print("no metrics found. run: python scripts/run_demo.py", file=sys.stderr)
        return 2

    config.REPORT_FILE.write_text(render(recorder))
    print(f"report written to {config.REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
