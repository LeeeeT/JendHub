import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

import numpy as np
from pydantic import BaseModel, ConfigDict, TypeAdapter

from jend import embed, jev, openrouter
from jend.cache import Cache
from jend.index import Document, Index, Scores, load
from jend.search import Result, Searcher

CONCURRENCY = 8


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True)

    package: str
    name: str
    path: str | None = None


class Labeled(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    expected: tuple[Expected, ...]


def accepted(index: Index, labeled: Labeled) -> set[str]:
    return {
        document.key
        for document in index.documents
        for entry in document.entries
        for expected in labeled.expected
        if (entry.package.name or entry.package.hash) == expected.package
        and entry.definition.name == expected.name
        and expected.path in (None, entry.path)
    }


@dataclass(frozen=True)
class Outcome:
    labeled: Labeled
    accepted: set[str]
    lexical_rank: int | None
    semantic_rank: int | None
    result: Result

    def rank(self) -> int | None:
        return _first(self.accepted, [hit.document for hit in self.result.ranking])

    def found_in_top(self, k: int) -> float:
        top = {hit.document.key for hit in self.result.ranking[:k]}
        return len(top & self.accepted) / min(k, len(self.accepted))


def _first(accepted: set[str], documents: list[Document]) -> int | None:
    return next((rank for rank, document in enumerate(documents) if document.key in accepted), None)


def _rank(index: Index, accepted: set[str], scores: Scores) -> int | None:
    order = np.argsort(-scores)
    rows = [row for row, document in enumerate(index.documents) if document.key in accepted]
    if not rows:
        return None
    positions = np.empty(len(order), dtype=np.int64)
    positions[order] = np.arange(len(order))
    return int(min(positions[rows]))


async def evaluate(data: Path, labeled_path: Path) -> None:
    index = load(data)
    items = TypeAdapter(list[Labeled]).validate_json(labeled_path.read_bytes())
    gate = asyncio.Semaphore(CONCURRENCY)
    async with openrouter.connect() as client, jev.connect() as judge:
        searcher = Searcher(index, Cache(data / "index" / "cache.sqlite"), client, judge)

        async def run(labeled: Labeled) -> Outcome:
            keys = accepted(index, labeled)
            async with gate:
                vectors, _ = await embed.embed(client, [embed.query_text(labeled.query)])
                result = await searcher.search(labeled.query)
            return Outcome(
                labeled,
                keys,
                _rank(index, keys, index.bm25.scores(labeled.query)),
                _rank(index, keys, index.vectors @ vectors[0]),
                result,
            )

        outcomes = await asyncio.gather(*(run(item) for item in items))
    report(list(outcomes))


def report(outcomes: list[Outcome]) -> None:
    positives = [outcome for outcome in outcomes if outcome.labeled.expected]
    negatives = [outcome for outcome in outcomes if not outcome.labeled.expected]
    print(f"{len(positives)} queries with a match, {len(negatives)} without")
    for label, ranks in (
        ("keyword", [outcome.lexical_rank for outcome in positives]),
        ("vector", [outcome.semantic_rank for outcome in positives]),
    ):
        found = [rank for rank in ranks if rank is not None]
        within = sum(rank < 50 for rank in found)
        print(f"  {label:8} in top 50: {within}/{len(positives)}, median rank {median(found):.0f}")
    ranks = [outcome.rank() for outcome in positives]
    print(
        f"  candidates contain a match: {sum(rank is not None for rank in ranks)}/{len(positives)}"
    )
    for k in (1, 3, 5):
        print(
            f"  Jev top {k}: {sum(rank is not None and rank < k for rank in ranks)}/{len(positives)}"
        )
    reciprocal = [0.0 if rank is None else 1 / (rank + 1) for rank in ranks]
    print(f"  mean reciprocal rank: {mean(reciprocal):.3f}")
    print(
        "  share of labeled definitions in the top 5:"
        f" {mean(outcome.found_in_top(5) for outcome in positives):.3f}"
    )
    print(
        "  best probability without a match:"
        f" {' '.join(f'{outcome.result.ranking[0].probability:.2f}' for outcome in negatives)}"
    )
    for outcome, rank in zip(positives, ranks):
        if rank != 0:
            top = outcome.result.ranking[0]
            print(
                f"  miss {outcome.labeled.query!r}: got {top.document.entry.package_label}"
                f" {top.document.entry.definition.name} ({top.probability:.2f}), expected rank {rank}"
            )
    for outcome in negatives:
        top = outcome.result.ranking[0]
        print(
            f"  no match {outcome.labeled.query!r}: best {top.document.entry.definition.name}"
            f" ({top.probability:.2f})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the search on labeled queries.")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--queries", type=Path, default=Path("data/queries.json"))
    args = parser.parse_args()
    asyncio.run(evaluate(args.data, args.queries))


if __name__ == "__main__":
    main()
