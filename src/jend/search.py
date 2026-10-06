import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jend import embed, openrouter
from jend.features import Candidates, Featurizer, Rows, normalize, positions
from jend.index import Document, load
from jend.ranker import Ranker, Scores

RESULTS = 50


@dataclass(frozen=True)
class Hit:
    document: Document
    probability: float


@dataclass(frozen=True)
class Result:
    query: str
    ranking: tuple[Hit, ...]


def order(ranker: Ranker, featurizer: Featurizer, candidates: Candidates) -> tuple[Rows, Scores]:
    scores = ranker.scores(candidates.features)
    documents = featurizer.index.documents
    package_ranks = np.array([documents[row].entry.rank for row in positions(candidates.rows)])
    best = np.lexsort((package_ranks, -scores))
    return candidates.rows[best], ranker.probabilities(scores[best])


@dataclass(frozen=True)
class Engine:
    featurizer: Featurizer
    ranker: Ranker
    embedder: embed.QueryEmbedder

    async def search(self, query: str) -> Result:
        vectors = await self.embedder.embed([normalize(query)])
        candidates = self.featurizer.candidates(query, vectors[0])
        rows, probabilities = order(self.ranker, self.featurizer, candidates)
        documents = self.featurizer.index.documents
        hits = tuple(
            Hit(documents[row], float(probability))
            for row, probability in zip(positions(rows[:RESULTS]), probabilities[:RESULTS])
        )
        return Result(normalize(query), hits)


async def _run(data: Path, query: str, top: int) -> None:
    featurizer = Featurizer(load(data))
    cache = embed.query_cache(data)
    async with openrouter.connect() as client:
        engine = Engine(featurizer, Ranker.load(), embed.QueryEmbedder(client, cache))
        result = await engine.search(query)
    cache.close()
    print(f"results for {result.query!r}")
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
