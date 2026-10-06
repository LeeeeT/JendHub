import math
import random
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

ANSWER = 2
EXACT = 3


class Judgment(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    grade: int
    definition: str
    note: str = ""


class Query(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    query: str
    style: str
    topic: str
    judgments: tuple[Judgment, ...]

    def grades(self) -> dict[str, int]:
        return {judgment.key: judgment.grade for judgment in self.judgments}

    def answers(self) -> set[str]:
        return {judgment.key for judgment in self.judgments if judgment.grade >= ANSWER}


_QUERIES: TypeAdapter[tuple[Query, ...]] = TypeAdapter(tuple[Query, ...])


def load(path: Path) -> tuple[Query, ...]:
    return _QUERIES.validate_json(path.read_bytes())


def dump(queries: Sequence[Query]) -> str:
    blocks: list[str] = []
    for query in queries:
        head = query.model_dump_json(exclude={"judgments"})
        ordered = sorted(query.judgments, key=lambda j: (-j.grade, j.definition, j.key))
        lines = ",\n".join(f"    {judgment.model_dump_json()}" for judgment in ordered)
        blocks.append(f'  {head[:-1]}, "judgments": [\n{lines}\n  ]}}')
    return "[\n" + ",\n".join(blocks) + "\n]\n"


def dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def ndcg(ranking: Sequence[str], grades: dict[str, int], cutoff: int) -> float:
    ideal = dcg(sorted(grades.values(), reverse=True)[:cutoff])
    if ideal == 0:
        return 0.0
    return dcg([grades.get(key, 0) for key in ranking[:cutoff]]) / ideal


def reciprocal_rank(ranking: Sequence[str], relevant: set[str]) -> float:
    return next((1 / (rank + 1) for rank, key in enumerate(ranking) if key in relevant), 0.0)


def recall(ranking: Sequence[str], relevant: set[str], cutoff: int) -> float:
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranking[:cutoff])) / len(relevant)


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    pairs = [
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    ]
    return sum(pairs) / len(pairs) if pairs else math.nan


def bootstrap(deltas: Sequence[float], resamples: int = 10_000) -> tuple[float, float]:
    generator = random.Random(0)
    means = sorted(
        sum(generator.choices(deltas, k=len(deltas))) / len(deltas) for _ in range(resamples)
    )
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]
