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

    cognify() runs LLM extraction per document, so ingest is slow and costs
    tokens. run_demo kicks it off in the background rather than blocking.
    """

    name = "cognee"

    def __init__(self):
        import cognee  # imported lazily so the stub path needs no dependency

        self._cognee = cognee
        if config.OPENAI_API_KEY:
            cognee.config.set_llm_provider("openai")
            cognee.config.set_llm_model("gpt-4o-mini")
            cognee.config.set_llm_api_key(config.OPENAI_API_KEY)

    async def ingest(self, corpus_dir: str) -> dict:
        paths = [
            str(p)
            for p in sorted(Path(corpus_dir).rglob("*"))
            if p.is_file() and p.suffix in (".md", ".json", ".csv")
        ]
        if not paths:
            return {"documents": 0, "mode": "real"}

        await self._cognee.add(
            paths,
            dataset_name=config.COGNEE_DATASET,
            node_set=["oncall", "corpus"],
        )
        await self._cognee.cognify(datasets=config.COGNEE_DATASET)
        return {"documents": len(paths), "mode": "real"}

    async def semantic_recall(self, error_text: str, k: int = 3) -> list[dict]:
        from cognee import SearchType

        results = await self._cognee.search(
            query_text=(
                "A data pipeline job failed with this error. What do our runbooks "
                f"and postmortems say about this failure and its fix?\n{error_text}"
            ),
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=config.COGNEE_DATASET,
            top_k=k,
        )
        return _normalise_results(results, k)

    async def add_resolution(self, text: str) -> None:
        """Write the resolution narrative back, so Cognee stays load-bearing
        across the session rather than being a one-time import."""
        await self._cognee.add(
            text,
            dataset_name=config.COGNEE_DATASET,
            node_set=["oncall", "resolution"],
        )


def _normalise_results(results, k: int) -> list[dict]:
    """Cognee's return shape varies by SearchType and version. Coerce to dicts
    without asserting a shape we have not verified."""
    if results is None:
        return []
    if isinstance(results, (str, bytes)):
        return [{"title": "cognee", "score": 1.0, "excerpt": str(results)[:400]}]
    if isinstance(results, dict):
        results = [results]

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
