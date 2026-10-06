import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from jend import embed, openrouter
from jend.index import Document, load
from jend.score import Scorer, normalize, positions

RESULTS = 50


@dataclass(frozen=True)
class Hit:
    document: Document
    score: float


@dataclass(frozen=True)
class Result:
    query: str
    ranking: tuple[Hit, ...]


@dataclass(frozen=True)
class Engine:
    scorer: Scorer
    embedder: embed.QueryEmbedder

    async def search(self, query: str) -> Result:
        vectors = await self.embedder.embed([normalize(query)])
        ranking = self.scorer.rank(query, vectors[0])
        documents = self.scorer.index.documents
        hits = tuple(
            Hit(documents[row], float(score))
            for row, score in zip(positions(ranking.rows[:RESULTS]), ranking.scores[:RESULTS])
        )
        return Result(normalize(query), hits)


async def _run(data: Path, query: str, top: int) -> None:
    scorer = Scorer(load(data))
    cache = embed.query_cache(data)
    async with openrouter.connect() as client:
        result = await Engine(scorer, embed.QueryEmbedder(client, cache)).search(query)
    cache.close()
    print(f"results for {result.query!r}")
    for hit in result.ranking[:top]:
        entry = hit.document.entry
        print(f"{hit.score:5.2f}  {entry.package_label}/{entry.path}:{entry.definition.line}")
        print(f"       {entry.definition.signature.splitlines()[0][:110]}")
        if hit.document.enrichment is not None:
            print(f"       {hit.document.enrichment.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search BendHub definitions.")
    parser.add_argument("query")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run(args.data, args.query, args.top))


if __name__ == "__main__":
    main()
