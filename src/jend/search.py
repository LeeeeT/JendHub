import argparse
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from jend import embed, openrouter
from jend.index import INDEX, Index, Record
from jend.score import Ranking, Scorer, normalize

PAGE = 20
RECENT_RANKINGS = 256


@dataclass(frozen=True)
class Hit:
    record: Record
    score: float


@dataclass(frozen=True)
class Result:
    query: str
    total: int
    start: int
    hits: tuple[Hit, ...]


class Engine:
    def __init__(self, scorer: Scorer, embedder: embed.QueryEmbedder) -> None:
        self.scorer = scorer
        self.embedder = embedder
        self.recent: OrderedDict[str, Ranking] = OrderedDict()

    async def ranking(self, query: str) -> Ranking:
        ranking = self.recent.get(query)
        if ranking is None:
            vectors = await self.embedder.embed([query])
            ranking = self.scorer.rank(query, vectors[0])
            self.recent[query] = ranking
            if len(self.recent) > RECENT_RANKINGS:
                self.recent.popitem(last=False)
        else:
            self.recent.move_to_end(query)
        return ranking

    async def search(self, query: str, start: int, count: int) -> Result:
        text = normalize(query)
        ranking = await self.ranking(text)
        page = slice(start, start + count)
        records = self.scorer.index.records(ranking.rows[page])
        hits = tuple(
            Hit(record, float(score))
            for record, score in zip(records, ranking.scores[page], strict=True)
        )
        return Result(text, len(ranking.rows), start, hits)


async def _run(data: Path, query: str, top: int) -> None:
    index = Index(data / INDEX)
    cache = embed.query_cache(data)
    async with openrouter.connect() as client:
        engine = Engine(Scorer(index), embed.QueryEmbedder(client, cache))
        result = await engine.search(query, 0, top)
    cache.close()
    index.close()
    print(f"{result.total} results for {result.query!r}")
    for hit in result.hits:
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
