# 03 — Verified SDK Surfaces

> Written after reading each vendor's current docs. **Do not invent method names.**
> If something you need is not in this file, read the vendor docs and add it here first.
> Fetched: see each section. All four are early-stage; treat this as a snapshot.

---

## Decision log (deviations from `02_SPEC.md`)

| Spec said | Reality | What we do |
|---|---|---|
| HydraDB serves OpenCypher, 3-hop `MATCH` (§4.3) | HydraDB v2 has **no graph query language**. Surface is `databases.*`, `context.*`, `query()`, `feedback.*` | Lineage graph is **ours** (`core/lineage.py`), traversed in Python. HydraDB becomes the **accumulator** for resolutions. |
| `cognee.export_graph()` → project into HydraDB (§6.2) | No such API exists | Cognee is the **corpus recall** layer via `search()`. No projection step. |
| Rote (Modiqo) for capture/replay | No public docs, no verified API | **Implemented in-house** as `core/muscle.py` (local JSON store, deterministic replay). |
| `contracts.py` is sync (§6.1) | RocketRide is asyncio/websockets; Cognee is async | **Async-first**: all adapter protocols are `async def`. |
| hotdata reads local Parquet | hotdata is a **cloud service** (upload → load → SQL) | Adapter uploads then queries. Stub path reads Parquet locally via DuckDB. |

### Why three memory layers (the Q&A answer)

- **Muscle memory** (`core/muscle.py`) stores **paths** — the executable step sequence. Exact signature match, deterministic replay, zero tokens.
- **Cognee** stores **prose** — runbooks and postmortems as a graph. Fuzzy, semantic. *What the org knew before we started.*
- **HydraDB** stores **outcomes** — signature, table, fix, timing. Fast, accumulating, queryable mid-run. *What the agent learned since.*

Different time horizons, not different query styles.

---

## Cognee

Docs: <https://docs.cognee.ai/python-api> · skill ref: `topoteretes/cognee/cognee/skill.md`

```bash
pip install cognee
```

**All functions are async and live on the top-level module.**

```python
import cognee
from cognee import SearchType

cognee.config.set_llm_provider("openai")
cognee.config.set_llm_model("gpt-4o-mini")
cognee.config.set_llm_api_key("sk-...")

await cognee.add("text | filepath | url", dataset_name="main", node_set=["tag"])
await cognee.cognify(datasets="main")
results = await cognee.search(
    query_text="...",
    query_type=SearchType.GRAPH_COMPLETION,
    datasets="main",
    top_k=10,
)
```

- v1.0 entry points: `remember()`, `recall()`, `improve()`, `forget()`, `serve()`, `push()`
- Legacy (we use these — more explicit): `add()`, `cognify()`, `search()`, `memify()`
- `SearchType` values used here: `GRAPH_COMPLETION` (default for graph Q&A), `CHUNKS` (fast semantic, no LLM completion), `CYPHER` (raw Cypher against Cognee's own graph store, only if enabled in config)
- `node_set=[...]` tags at `add()` time become first-class graph nodes after `cognify()`; filter at search with `node_name=[...]`
- Pruning: `await cognee.prune.prune_data()`, `await cognee.prune.prune_system(graph=True, vector=True)`
- **`cognify()` calls an LLM per document.** 30 docs is minutes and real tokens. Run in background.

**Verified against a live hosted tenant (cognee 1.5.4):**
- **No OpenAI key needed in cloud mode.** `await cognee.serve(url=..., api_key=...)` attaches the SDK to a hosted tenant and extraction runs on their infrastructure. `OPENAI_API_KEY` is only for local open-source mode.
- **`datasets` must be a LIST against a hosted tenant.** Passing the bare string is accepted locally but the remote API returns `422 {"detail":[{"type":"list_type","loc":["body","datasets"],"msg":"Input should be a valid list"}]}`. Applies to both `search()` and `cognify()`.
- **The hosted response is wrapped per dataset:** `[{dataset_id, dataset_name, dataset_tenant_id, search_result: [...]}]`. The prose lives in `search_result`. Unwrap it, or callers get raw JSON as the excerpt.
- Verify credentials before wiring: `curl $URL/health` (expect 200) and `curl $URL/api/v1/datasets/ -H "X-Api-Key: $KEY"` (200 = good, 401 = bad key, 404/5xx = wrong URL).
- `GRAPH_COMPLETION` needs a built graph; it fails before `cognify()` completes. Fall back to `SearchType.CHUNKS` so recall degrades instead of blocking an incident.
- Call `await cognee.disconnect()` at shutdown or aiohttp warns about an unclosed session.
- Timings on 12 short documents: ingest ~9s total, `GRAPH_COMPLETION` recall ~9-17s per query.

## HydraDB

Docs: <https://docs.hydradb.com/AGENTS> (LLM-facing guide — the authoritative one)

```bash
pip install "hydradb-sdk>=2,<3"
```

```python
from hydra_db import HydraDB
client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
```

- Base URL `https://api.hydradb.com`; raw HTTP needs `Authorization: Bearer <key>` **and** `API-Version: 2`. SDKs set the version header themselves.
- **Every response is an envelope**: `{success, data, error, meta}`. Read payloads from `.data`. Log `meta.request_id` on failure.
- **No Cypher. No labeled nodes or typed edges.** Retrieval is hybrid semantic + BM25 over chunks.

Full surface:

```
client.databases.create(database=...)        # async — poll status
client.databases.status(database=...)         # .data.infra.ready_for_ingestion
client.databases.list() / .collections() / .stats() / .delete()
client.context.ingest(type="knowledge"|"memory", database=..., collection=..., ...)
client.context.status(database=..., collection=..., ids=[...])
client.context.list() / .inspect() / .relations() / .delete()
client.query(database=..., query=..., type="knowledge"|"memory"|"all", ...)
client.feedback.submit(request_id=..., source="agent", rating=..., feedback=...)
```

Two async lifecycles, both must be polled:
1. `databases.create()` → poll `databases.status()` until `data.infra.ready_for_ingestion`
2. `context.ingest()` → poll `context.status()` until `indexing_status` in `("graph_creation", "completed")`. Searchable at `graph_creation`. Terminal failures: **both** `errored` and `failed`.

Gotchas that cost real time:
- `collection` must match between write and read. Wrong/missing `collection` on `context.status()` returns `indexing_status: "errored"` with `error_code: "FILE_NOT_FOUND"` — a scope miss, not an indexing failure. Branch on `error_code`, not message text.
- `memories` / `app_knowledge` / `document_metadata` are **JSON-stringified** strings, not objects.
- `query_forceful_relations` only works in `mode: "thinking"`.
- Retry only 429/500/503 with bounded backoff.
- Query params: `type`, `query_by` (`hybrid`|`text`), `mode` (`fast`|`thinking`), `max_results` (≤50), `alpha`, `recency_bias`, `graph_context`, `metadata_filters`.

Response shape: `.data.chunks[]`, `.data.sources[]`, `.data.graph_context{query_paths, chunk_relations}`. Preserve server ordering of `chunks`.

**Verified against the live API (not just docs):**
- **The dashboard snippet is wrong.** It shows `from hydradb import HydraDB` with `api_key=`. The installed package is `hydra_db` and the kwarg is `token=`. There is no `hydradb` module.
- Use **`AsyncHydraDB`** — a native async client exists, so no thread wrapping is needed. `HydraDB` is the sync equivalent.
- `databases.stats()` returns a `TenantsTenantStatsResponse`: the memory count is at **`data.memory_collection.row_count`**, alongside `knowledge_collection.row_count`. Keys like `memories` / `memory_count` / `total` do not exist.
- **Indexing lag is real and matters.** A memory written via `context.ingest` is not immediately searchable — `context.status` reports `graph_creation` after a few seconds, and only then does `query` return it. A recall issued immediately after a write returns zero chunks. Do not block the loop waiting; treat HydraDB as the durable record and let visibility be eventual.
- Because of that lag, `count()` returns `max(server_count, written_count)` so the accumulating-memory figure never appears to go backwards mid-run.
- `additional_metadata` survives the round trip intact and comes back as a plain dict on each chunk.
- `context.delete(type="memory", database=..., ids=[...])` works for cleanup.

## hotdata.dev

Docs: <https://www.hotdata.dev/docs/python-sdk> · <https://www.hotdata.dev/docs/quick-start>

```bash
pip install hotdata          # 'hotdata[arrow]' for pyarrow results
brew install hotdata-dev/tap/cli   # CLI, useful for smoke tests
```

```python
import hotdata
configuration = hotdata.Configuration(
    api_key="...", workspace_id="...",   # host defaults to https://api.hotdata.dev
)
with hotdata.ApiClient(configuration) as api_client:
    query_api = hotdata.QueryApi(api_client)
    resp = query_api.query(
        hotdata.QueryRequest(sql="SELECT 1 AS ok"),
        x_database_id=database_id,
    )
```

- **A database scope is mandatory.** Send `x_database_id` header *or* `database_id` body field, exactly one. Both disagreeing = 400.
- Tables address as `<catalog>.<schema>.<table>`, e.g. `mydb.public.orders`.
- Three-step load: `DatabasesApi.create_database(...)` → `UploadsApi.upload_file(path, content_type=...)` → `ConnectionsApi.load_managed_table(connection_id, var_schema="public", table=..., request(mode="replace", upload_id=...))`. Note `var_schema`, not `schema`.
- `create_database` takes `expires_at="24h"` and nested `DatabaseDefaultSchemaDecl(name="public", tables=[DatabaseDefaultTableDecl(name=...)])`.
- `DatabasesApi.load_database_table(database_id, ...)` skips fetching `default_connection_id` first.
- Errors: `from hotdata.rest import ApiException`.

**Verified against the live API (not just docs):**
- **Do not prefix the `--catalog`/`name` alias in SQL.** The catalog is always `default`, so `oncall.public.orders_today` raises `BAD_REQUEST: table not found`. All of these work: bare `orders_today`, `public.orders_today`, `default.public.orders_today`. Since `x_database_id` already scopes the query, use the bare name.
- `load_managed_table` returns `row_count`, `table_name`, `schema_name`, `connection_id`, and `arrow_schema_json`. A successful load reporting 20000 rows still 404s on query if the SQL names the wrong catalog — check the qualification before suspecting the load.
- `InformationSchemaApi` exposes `information_schema()`, **not** `list_tables()`, and it does not accept `x_database_id`.
- `create_database` 409s on a duplicate name, so `setup()` lists databases and reuses a match rather than creating one per run.
- API token needs **Read + Write** access: `create_database`, `upload_file`, and `load_managed_table` are all writes.
- **Uploads cost wall-clock.** Keep row counts modest; all our diagnostics are relative, so 20–50k rows proves the same thing as 200k.

CLI equivalent (for `smoke.py`):

```bash
hotdata auth login && hotdata auth status
hotdata databases create --name mydb --catalog mydb --table orders
hotdata databases load --catalog mydb --table orders --file orders.parquet
hotdata query "SELECT count(*) FROM mydb.public.orders"
```

## RocketRide

Docs: <https://docs.rocketride.org/develop/python>

```bash
pip install rocketride
```

```python
from rocketride import RocketRideClient

async with RocketRideClient(uri="https://cloud.rocketride.ai", auth=key) as client:
    result = await client.use(filepath="pipeline.pipe")   # or pipeline={...}
    token = result["token"]
    out = await client.send(token, "payload",
                            objinfo={"name": "input.txt"}, mimetype="text/plain")
    await client.terminate(token)
```

- **asyncio-first**, built on `websockets`. Prefer `async with`.
- **URI scheme matters for security:** `https://` and `wss://` → encrypted `wss://`. `http://`, `ws://`, or bare `host:port` **silently downgrade to unencrypted**. Always `https://` or `wss://` against Cloud.
- Env: `ROCKETRIDE_URI`, `ROCKETRIDE_APIKEY`.
- A pipeline run is identified by `token` from `use()`; every subsequent call targets it.
- `send()` one-shot; `pipe()` for streaming (`open` → `write`* → `close`); `send_files()` for uploads.
- `get_task_status(token)` → `completedCount`, `totalCount`, `completed`, `state`.
- `set_events(token, [...])` + `on_event` callback for progress.
- Exceptions: `DAPException` → `RocketRideException` → {`ConnectionException` → `AuthenticationException`, `PipeException`, `ExecutionException`, `ValidationException`}. Branch on `e.code` (e.g. `TASK_NOT_REGISTERED`), never on message text. `e.hint` is developer-only detail.
- Docs reference `cloud.rocketride.ai`; `01_GROUND.md` says `staging.rocketride.ai`. **Verify which the account is on.**

---

## Environment variables

```
OPENAI_API_KEY=            # Cognee's LLM provider
HYDRA_DB_API_KEY=
HOTDATA_API_KEY=
HOTDATA_WORKSPACE_ID=
ROCKETRIDE_URI=https://cloud.rocketride.ai
ROCKETRIDE_APIKEY=
```

Per-adapter stub flags (`core/config.py`) — each flips independently:

```
USE_STUB_COGNEE=1
USE_STUB_HYDRA=1
USE_STUB_HOTDATA=1
USE_STUB_ROCKETRIDE=1
```
