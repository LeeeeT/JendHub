import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from jend import embed, openrouter
from jend.index import INDEX, Index, Record
from jend.score import Scorer, normalize

RESULTS = 50


@dataclass(frozen=True)
class Hit:
    record: Record
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
        records = self.scorer.index.records(ranking.rows[:RESULTS])
        hits = tuple(
            Hit(record, float(score))
            for record, score in zip(records, ranking.scores[:RESULTS], strict=True)
        )
        return Result(normalize(query), hits)


async def _run(data: Path, query: str, top: int) -> None:
    index = Index(data / INDEX)
    cache = embed.query_cache(data)
    async with openrouter.connect() as client:
        result = await Engine(Scorer(index), embed.QueryEmbedder(client, cache)).search(query)
    cache.close()
    index.close()
    print(f"results for {result.query!r}")
    for hit in result.ranking[:top]:
        record = hit.record
        print(f"{hit.score:5.2f}  {record.package_label}/{record.path}:{record.line}")
        print(f"       {record.signature.splitlines()[0][:110]}")
        if record.summary is not None:
            print(f"       {record.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search BendHub definitions.")
    parser.add_argument("query")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run(args.data, args.query, args.top))


if __name__ == "__main__":
    main()
