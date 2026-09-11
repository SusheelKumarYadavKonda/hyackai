/* Compounding On-Call — live UI.
   Consumes a server-sent event stream from the running agent. No framework:
   the whole point is that what you see is the actual run, not a recording. */

const $  = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const TABLES = ["orders", "shipments", "inventory"];
const state = { runs: [], maxPaths: 1, maxMem: 1 };
const inflight = new Map(); // layer -> { li, tick } for calls still running

/* ---------- pipeline tiles ---------- */

function buildJobs() {
  const box = $("jobs");
  box.innerHTML = "";
  for (const t of TABLES) {
    const row = el("div", "job");
    row.id = `job-${t}`;
    row.append(el("span", "jdot"), el("span", null, `job_${t}_daily`), el("span", "jstate", "idle"));
    box.append(row);
  }
}

function setJob(table, cls, label) {
  const row = $(`job-${table}`);
  if (!row) return;
  row.className = `job ${cls}`;
  row.querySelector(".jstate").textContent = label;
}

function setCheck(name, cls, label) {
  const li = document.querySelector(`.checks li[data-check="${name}"] .pill`);
  if (!li) return;
  li.className = `pill ${cls}`;
  li.textContent = label;
}

function resetChecks() {
  ["not_null", "volume", "freshness"].forEach((c) => setCheck(c, "idle", "idle"));
}

/* ---------- step timeline ---------- */

function step(n, cls, html) {
  const li = document.querySelector(`.steps li[data-step="${n}"]`);
  if (!li) return;
  li.classList.remove("active", "done", "skip", "crash", "hit");
  if (cls) li.classList.add(...cls.split(" "));
  if (html !== undefined) $(`s${n}`).innerHTML = html;
}

function resetSteps() {
  for (let n = 1; n <= 8; n++) step(n, "", "—");
}

/* ---------- curve ---------- */

function renderCurve() {
  const cold = state.runs.filter((r) => r.path === "COLD");
  const warm = state.runs.filter((r) => r.path === "WARM");
  const avg = (xs, k) => (xs.length ? xs.reduce((a, b) => a + b[k], 0) / xs.length : 0);

  const coldMs = avg(cold, "elapsed_ms"), warmMs = avg(warm, "elapsed_ms");
  const coldTok = avg(cold, "tokens");
  const scale = Math.max(coldMs, warmMs, 1);

  $("k-resolved").textContent = `${state.runs.filter((r) => r.resolved).length}/${state.runs.length}`;
  $("k-speedup").textContent = warmMs ? `${(coldMs / warmMs).toFixed(1)}×` : "—";
  $("k-saved").textContent = Math.round(warm.length * coldTok).toLocaleString();

  const bars = $("bars");
  bars.innerHTML = "";
  [["COLD reasoned", cold, coldMs, "cold"], ["WARM replayed", warm, warmMs, "warm"]].forEach(
    ([label, rows, ms, cls]) => {
      const row = el("div", "bar-row");
      const lbl = el("div", "lbl");
      lbl.append(el("span", null, label), el("span", null, `${Math.round(ms).toLocaleString()}ms · ${rows.length} run(s)`));
      const track = el("div", "bar-track");
      const fill = el("i", cls);
      fill.style.width = `${Math.max((ms / scale) * 100, ms ? 2 : 0)}%`;
      track.append(fill);
      row.append(lbl, track);
      bars.append(row);
    }
  );
}

function addRunRow(r) {
  const tr = el("tr");
  tr.append(
    el("td", null, r.run_no),
    el("td", null, r.table),
    el("td", r.path === "COLD" ? "m-cold" : "m-warm", r.path),
    el("td", null, r.elapsed_ms.toLocaleString()),
    el("td", null, r.tokens.toLocaleString())
  );
  $("runs").append(tr);
}

/* ---------- memory counters ---------- */

function setMem({ paths, memories, corpus }) {
  if (paths !== undefined) {
    $("m-paths").textContent = paths;
    state.maxPaths = Math.max(state.maxPaths, paths);
    $("sp-paths").innerHTML = `<i style="width:${(paths / state.maxPaths) * 100}%"></i>`;
  }
  if (memories !== undefined) {
    $("m-mem").textContent = memories;
    state.maxMem = Math.max(state.maxMem, memories);
    $("sp-mem").innerHTML = `<i style="width:${(memories / state.maxMem) * 100}%"></i>`;
  }
  if (corpus !== undefined) $("m-corpus").textContent = corpus;
}

/* ---------- event handlers ---------- */

const handlers = {
  layers(d) {
    const box = $("layers");
    box.innerHTML = "";
    for (const [name, mode] of Object.entries(d.layers)) {
      box.append(el("span", `layer ${mode === "REAL" || mode === "IN-HOUSE" ? "real" : "stub"}`, `${name} ${mode}`));
    }
  },

  ingested(d) { setMem({ corpus: d.documents }); },

  reset(d) {
    setMem({ paths: 0, memories: 0 });
    $("footnote").textContent = d.note || "";
  },

  job_start(d) {
    setJob(d.table, "running", "running");
    $("stage-status").textContent = `running job_${d.table}_daily`;
    $("stage-status").className = "status busy";
  },

  incident(d) {
    $("empty").classList.add("hidden");
    $("incident-card").classList.remove("hidden");
    resetSteps();
    resetChecks();
    $("inc-no").textContent = d.run_no;
    $("inc-job").textContent = d.job_id;
    $("inc-sig").textContent = "fingerprinting…";
    const badge = $("inc-path");
    badge.className = "path-badge";
    badge.textContent = "…";
    $("inc-ms").textContent = "—";
    $("inc-tokens").textContent = "—";

    setJob(d.table, "failed", `exit ${d.exit_code}`);
    step(1, "crash", `<span class="bad">exit ${d.exit_code}</span> — ${escapeHtml(d.error_line)}`);
    if (/not null/i.test(d.error_line)) setCheck("not_null", "fail", "fail");
    if (/row count/i.test(d.error_line)) setCheck("volume", "fail", "fail");
  },

  fingerprint(d) {
    $("inc-sig").textContent = `${d.signature} · ${d.sig_type}`;
    step(2, "done", `<span class="num">${d.signature}</span> — ${d.sig_type}, table name stripped so the same fault matches anywhere`);
    step(3, "active", "checking…");
  },

  memory(d) {
    const badge = $("inc-path");
    if (d.hit) {
      badge.className = "path-badge warm";
      badge.textContent = "WARM";
      step(3, "hit", `<span class="ok">HIT</span> — solved this shape on <b>${d.from_table}</b>, replaying with no reasoning`);
      [4, 5, 6].forEach((n) => step(n, "skip", "skipped — replaying a known fix"));
    } else {
      badge.className = "path-badge cold";
      badge.textContent = "COLD";
      step(3, "done", `<span class="num">MISS</span> — never seen this shape, reasoning from scratch`);
      step(4, "active", "querying memory…");
    }
  },

  recall(d) {
    step(4, "done",
      `lineage <span class="num">${d.hops}</span> upstream change(s), owner ${d.owner} · ` +
      `cognee <span class="num">${d.corpus}</span> doc(s) · hydradb <span class="num">${d.fixes}</span> prior fix(es)`);
    step(5, "active", "querying live data…");
  },

  diagnose(d) {
    step(5, "done", escapeHtml(d.detail) + (d.failed_check ? ` <span class="bad">[${d.failed_check}]</span>` : ""));
    step(6, "active", "deciding…");
  },

  act(d) {
    step(6, "done",
      `<b>${d.action}</b> <span class="why">because ${escapeHtml(d.rationale)}</span><br>` +
      d.chain.map((s) => `<span class="ok">${s}</span>`).join(" → "));
    step(7, "active", "re-running the job…");
  },

  verify(d) {
    step(7, d.passed ? "done" : "crash",
      d.passed
        ? `<span class="ok">exit 0 — resolved</span>, confirmed by re-running the real job`
        : `<span class="bad">exit ${d.exit_code} — still failing</span>`);
    setJob(d.table, d.passed ? "fixed" : "failed", d.passed ? "resolved" : "failed");
    if (d.passed) {
      setCheck("not_null", "pass", "pass");
      setCheck("volume", "pass", "pass");
      setCheck("freshness", "pass", "pass");
    }
  },

  learn(d) {
    step(8, "done", d.items.map((i) => `<span class="ok">${escapeHtml(i)}</span>`).join("<br>"));
  },

  call_start(d) {
    const li = el("li", "call inflight");
    li.dataset.layer = d.layer;
    li.id = `call-${d.layer}-${Date.now()}`;
    li.append(el("span", "who", d.layer), el("span", "what", d.question), el("span", "ms", "0.0s"));
    $("calls").prepend(li);

    // Live timer, so a 20s call visibly counts rather than sitting frozen.
    const t0 = performance.now();
    const tick = setInterval(() => {
      li.querySelector(".ms").textContent = `${((performance.now() - t0) / 1000).toFixed(1)}s`;
    }, 100);
    inflight.set(d.layer, { li, tick });

    // Keep the list short enough to read from the back of a room.
    const all = $("calls").children;
    while (all.length > 9) all[all.length - 1].remove();
  },

  call_end(d) {
    const entry = inflight.get(d.layer);
    if (!entry) return;
    clearInterval(entry.tick);
    entry.li.classList.remove("inflight");
    entry.li.classList.add(d.error ? "failed" : "done");
    entry.li.querySelector(".ms").textContent = d.error
      ? d.error
      : d.ms >= 1000
      ? `${(d.ms / 1000).toFixed(1)}s`
      : `${d.ms}ms`;
    inflight.delete(d.layer);
  },

  run(d) {
    if (d.path === "WARM") step(8, "skip", "already captured — nothing new to learn");
    $("inc-ms").textContent = `${d.elapsed_ms.toLocaleString()} ms`;
    $("inc-tokens").textContent = `${d.tokens.toLocaleString()} tokens`;
    state.runs.push(d);
    addRunRow(d);
    renderCurve();
    setMem({ paths: d.replay_paths, memories: d.memory_nodes });
  },

  done(d) {
    $("stage-status").textContent = `${d.resolved}/${d.runs} resolved`;
    $("stage-status").className = "status done";
    $("live-dot").classList.remove("live");
    $("run").disabled = false;
    $("run").textContent = "Run again";
    $("footnote").textContent =
      `Layers: ${d.layer_line}. Token counts on stubbed layers are estimated from ` +
      `real context size. Every verify re-ran the actual job as a subprocess.`;
  },
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------- wire up ---------- */

buildJobs();
renderCurve();

const stream = new EventSource("/events");
stream.onmessage = (msg) => {
  const ev = JSON.parse(msg.data);
  const fn = handlers[ev.kind];
  if (fn) fn(ev.data);
};

$("run").addEventListener("click", async () => {
  $("run").disabled = true;
  $("run").textContent = "Running…";
  $("live-dot").classList.add("live");
  $("runs").innerHTML = "";
  $("calls").innerHTML = "";
  inflight.forEach(({ tick }) => clearInterval(tick));
  inflight.clear();
  state.runs = [];
  renderCurve();
  await fetch("/start", { method: "POST" });
});
