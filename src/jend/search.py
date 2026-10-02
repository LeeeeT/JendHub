import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import numpy as np
from typesafe_sdk import AsyncTypeSafeClient, Noul, NoulCriteria

from jend import embed, jev, openrouter
from jend.cache import Cache
from jend.index import Document, Index, Scores, load

CANDIDATES = 50
FUSION_DEPTH = 100
RRF_K = 60
SIGNATURE_CHARS = 600
DESCRIPTION_CHARS = 120

INSTRUCTIONS = (
    "Would a programmer call definition `definitions.{tag}` directly to do what `query` asks for?"
)
MATCH = NoulCriteria(
    true="Calling the definition does what `query` asks for, or a central part of it",
    false=(
        "The definition does something else, only uses or mentions the subject, or is an"
        " internal helper that performs one step for another definition, such as a loop"
        " or a state of a parser"
    ),
)
QUESTION_VERSION = hashlib.sha256(
    json.dumps([INSTRUCTIONS, MATCH], sort_keys=True).encode()
).hexdigest()[:12]


@dataclass(frozen=True)
class Hit:
    document: Document
    probability: float


@dataclass(frozen=True)
class Result:
    query: str
    ranking: tuple[Hit, ...]
    cached: bool
    cost: float


def normalize(query: str) -> str:
    return " ".join(query.lower().split())


def fuse(index: Index, lexical: Scores, semantic: Scores) -> list[Document]:
    scores: dict[int, float] = {}
    for ranking in (_top(lexical), _top(semantic)):
        for rank, row in enumerate(ranking):
            scores[row] = scores.get(row, 0.0) + 1.0 / (RRF_K + rank + 1)
    ordered = sorted(scores, key=lambda row: (-scores[row], index.documents[row].entry.rank))
    return [index.documents[row] for row in ordered[:CANDIDATES]]


def _top(scores: Scores) -> list[int]:
    depth = min(FUSION_DEPTH, len(scores))
    rows = np.argpartition(-scores, depth - 1)[:depth]
    return [int(row) for row in rows[np.argsort(-scores[rows])] if scores[row] > 0]


async def rerank(
    client: AsyncTypeSafeClient, query: str, documents: list[Document]
) -> tuple[list[Hit], float]:
    tags = [f"d{position:02d}" for position in range(len(documents))]
    response = await client.system_one(  # pyright: ignore[reportUnknownMemberType]
        state={
            "query": query,
            "definitions": {tag: _card(document) for tag, document in zip(tags, documents)},
        },
        questions={
            tag: Noul(instructions=INSTRUCTIONS.format(tag=tag), criteria=MATCH) for tag in tags
        },
    )
    hits = [Hit(document, response.nouls[tag].noul) for tag, document in zip(tags, documents)]
    cost = (response.usage.input_tokens or 0) * jev.USD_PER_INPUT_TOKEN
    return sorted(hits, key=lambda hit: hit.probability, reverse=True), cost


def _card(document: Document) -> dict[str, str]:
    entry = document.entry
    card = {
        "name": entry.definition.name,
        "package": entry.package_label,
        "package_description": entry.package.description[:DESCRIPTION_CHARS],
        "file": entry.path,
        "doc": entry.definition.doc,
        "signature": entry.definition.signature[:SIGNATURE_CHARS],
    }
    if document.enrichment is not None:
        card["summary"] = document.enrichment.summary
    return {name: value for name, value in card.items() if value}


@dataclass
class Searcher:
    index: Index
    cache: Cache
    openrouter: httpx2.AsyncClient
    jev: AsyncTypeSafeClient
    by_key: dict[str, Document] = field(init=False)
    cache_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.by_key = {document.key: document for document in self.index.documents}
        self.cache_id = f"{self.index.id}:{QUESTION_VERSION}"

    async def candidates(self, query: str) -> tuple[list[Document], float]:
        vectors, cost = await embed.embed(self.openrouter, [embed.query_text(query)])
        scores = self.index.vectors @ vectors[0]
        return fuse(self.index, self.index.bm25.scores(query), scores), cost

    def lookup(self, query: str) -> Result | None:
        query = normalize(query)
        stored = self.cache.get(self.cache_id, query)
        if stored is None:
            return None
        ranking = tuple(Hit(self.by_key[key], probability) for key, probability in stored)
        return Result(query, ranking, cached=True, cost=0.0)

    async def compute(self, query: str) -> Result:
        query = normalize(query)
        documents, embedding_cost = await self.candidates(query)
        hits, rerank_cost = await rerank(self.jev, query, documents)
        self.cache.put(self.cache_id, query, [(hit.document.key, hit.probability) for hit in hits])
        return Result(query, tuple(hits), cached=False, cost=embedding_cost + rerank_cost)

    async def search(self, query: str) -> Result:
        return self.lookup(query) or await self.compute(query)


async def _run(data: Path, query: str, top: int) -> None:
    index = load(data)
    async with openrouter.connect() as client, jev.connect() as judge:
        searcher = Searcher(index, Cache(data / "index" / "cache.sqlite"), client, judge)
        result = await searcher.search(query)
    print(f"results for {result.query!r}{' (cached)' if result.cached else ''}")
    for hit in result.ranking[:top]:
        entry = hit.document.entry
        print(f"{hit.probability:.2f}  {entry.package_label}/{entry.path}:{entry.definition.line}")
        print(f"      {entry.definition.signature.splitlines()[0][:110]}")
        if hit.document.enrichment is not None:
            print(f"      {hit.document.enrichment.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search BendHub definitions.")
    parser.add_argument("query")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run(args.data, args.query, args.top))


if __name__ == "__main__":
    main()
