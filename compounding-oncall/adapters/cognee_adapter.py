"""Cognee: unstructured org knowledge -> searchable graph.

Answers "what did the organisation already know about this failure class?"
Runbooks, postmortems, and Slack threads written before the incident.

Verified surface (docs/03_SDK_NOTES.md): async add() -> cognify() -> search().
There is no export_graph(), so spec §6.2's projection step does not exist; the
lineage graph is ours instead (core/lineage.py).
"""

import json
import re
from pathlib import Path

from core import config


class CogneeStub:
    """Keyword recall over the raw corpus files.

    Deliberately not embeddings: the stub must be explainable as "we scored
    keyword overlap", not mistaken for semantic search that isn't running.
    """

    name = "cognee(stub)"

    def __init__(self, corpus_dir: Path):
        self.corpus_dir = corpus_dir
        self._docs: list[dict] = []
        self._resolutions: list[str] = []

    async def ingest(self, corpus_dir: str) -> dict:
        self.corpus_dir = Path(corpus_dir)
        self._docs = []
        if not self.corpus_dir.exists():
            return {"documents": 0, "mode": "stub"}

        for path in sorted(self.corpus_dir.rglob("*")):
            if path.suffix not in (".md", ".json", ".csv") or not path.is_file():
                continue
            self._docs.append({"title": path.name, "text": path.read_text()[:4000]})
        return {"documents": len(self._docs), "mode": "stub"}

    async def semantic_recall(self, error_text: str, k: int = 3) -> list[dict]:
        terms = {t for t in re.findall(r"[a-z_]{4,}", error_text.lower())}
        scored = []
        for doc in self._docs:
            body = doc["text"].lower()
            overlap = sum(1 for t in terms if t in body)
            if overlap:
                scored.append((overlap, doc))
        scored.sort(key=lambda pair: -pair[0])
        return [
            {"title": doc["title"], "score": score, "excerpt": doc["text"][:220]}
            for score, doc in scored[:k]
        ]

    async def add_resolution(self, text: str) -> None:
        self._resolutions.append(text)
        self._docs.append({"title": "resolution (this session)", "text": text})


class CogneeReal:
    """Live Cognee. add() -> cognify() -> search() with SearchType.

    Two modes:
      cloud -- cognee.serve(url, api_key) points the SDK at a hosted tenant that
               performs LLM extraction on their infrastructure. No OPENAI_API_KEY
               required; the workspace subscription covers it.
      local -- open-source mode runs extraction on this machine, which means we
               must supply an LLM provider ourselves (defaults to OpenAI).

    cognify() calls a model per document either way, so ingest is slow. It runs
    once at startup rather than per incident.
    """

    name = "cognee"

    def __init__(self):
        import cognee  # imported lazily so the stub path needs no dependency

        self._cognee = cognee
        self._cloud = bool(config.COGNEE_API_KEY and config.COGNEE_BASE_URL)
        self._served = False

        if not self._cloud and config.OPENAI_API_KEY:
            # Local mode only: configure both LLM and embeddings, since setting
            # just one silently falls the other back to OpenAI defaults.
            cognee.config.set_llm_provider("openai")
            cognee.config.set_llm_model("gpt-4o-mini")
            cognee.config.set_llm_api_key(config.OPENAI_API_KEY)

    async def _ensure_served(self) -> None:
        """Attach to the hosted tenant once, before the first operation."""
        if self._cloud and not self._served:
            await self._cognee.serve(
                url=config.COGNEE_BASE_URL,
                api_key=config.COGNEE_API_KEY,
            )
            self._served = True

    async def ingest(self, corpus_dir: str) -> dict:
        await self._ensure_served()

        paths = [
            str(p)
            for p in sorted(Path(corpus_dir).rglob("*"))
            if p.is_file() and p.suffix in (".md", ".json", ".csv")
        ]
        mode = "cloud" if self._cloud else "local"
        if not paths:
            return {"documents": 0, "mode": mode}

        await self._cognee.add(
            paths,
            dataset_name=config.COGNEE_DATASET,
            node_set=["oncall", "corpus"],
        )
        # datasets as a list, for the same reason as in semantic_recall.
        await self._cognee.cognify(datasets=[config.COGNEE_DATASET])
        return {"documents": len(paths), "mode": mode}

    async def semantic_recall(self, error_text: str, k: int = 3) -> list[dict]:
        from cognee import SearchType

        await self._ensure_served()

        query = (
            "A data pipeline job failed with this error. What do our runbooks "
            f"and postmortems say about this failure and its fix?\n{error_text}"
        )
        # datasets must be a LIST against a hosted tenant. Passing the bare
        # string is accepted locally but the remote API rejects it with a 422
        # ("Input should be a valid list"). Verified against the live tenant.
        datasets = [config.COGNEE_DATASET]

        try:
            results = await self._cognee.search(
                query_text=query,
                query_type=SearchType.GRAPH_COMPLETION,
                datasets=datasets,
                top_k=k,
            )
        except Exception:  # noqa: BLE001
            # GRAPH_COMPLETION needs a built graph. Before cognify() finishes,
            # fall back to chunk retrieval so recall degrades rather than fails
            # -- an incident must never be blocked by the memory layer.
            results = await self._cognee.search(
                query_text=query,
                query_type=SearchType.CHUNKS,
                datasets=datasets,
                top_k=k,
            )
        return _normalise_results(results, k)

    async def add_resolution(self, text: str) -> None:
        """Write the resolution narrative back, so Cognee stays load-bearing
        across the session rather than being a one-time import."""
        await self._ensure_served()
        await self._cognee.add(
            text,
            dataset_name=config.COGNEE_DATASET,
            node_set=["oncall", "resolution"],
        )

    async def close(self) -> None:
        """Release the tenant connection. Credentials stay cached for next run."""
        if self._served:
            await self._cognee.disconnect()
            self._served = False


def _normalise_results(results, k: int) -> list[dict]:
    """Cognee's return shape varies by SearchType and deployment. Coerce to dicts
    without asserting a shape we have not verified.

    A hosted tenant wraps answers per dataset:
        [{dataset_id, dataset_name, dataset_tenant_id, search_result: [...]}]
    Verified against the live tenant. Unwrap that before anything else, or the
    caller receives raw JSON as the excerpt and downstream matching sees noise
    instead of prose.
    """
    if results is None:
        return []
    if isinstance(results, (str, bytes)):
        return [{"title": "cognee", "score": 1.0, "excerpt": str(results)[:400]}]
    if isinstance(results, dict):
        results = [results]

    unwrapped: list = []
    for entry in results if isinstance(results, list) else [results]:
        if isinstance(entry, dict) and "search_result" in entry:
            name = str(entry.get("dataset_name") or "cognee")
            inner = entry.get("search_result") or []
            for answer in inner if isinstance(inner, list) else [inner]:
                unwrapped.append({"name": name, "text": answer})
        else:
            unwrapped.append(entry)
    results = unwrapped

    out = []
    for item in list(results)[:k]:
        if isinstance(item, dict):
            excerpt = item.get("text") or item.get("content") or json.dumps(item)[:400]
            out.append(
                {
                    "title": str(item.get("name") or item.get("title") or "cognee"),
                    "score": float(item.get("score") or 1.0),
                    "excerpt": str(excerpt)[:400],
                }
            )
        else:
            out.append({"title": "cognee", "score": 1.0, "excerpt": str(item)[:400]})
    return out


def build(corpus_dir: Path):
    if config.USE_STUB_COGNEE:
        return CogneeStub(corpus_dir)
    return CogneeReal()
