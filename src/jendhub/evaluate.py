import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from jendhub.bench import BenchError, Query

ALL_KINDS = "all"
BOOTSTRAP_SAMPLES = 10_000
CONFIDENCE = 0.95
SEED = 0


class Pipeline(Protocol):
    def search(self, queries: list[str], limit: int) -> list[list[str]]: ...


@dataclass(frozen=True)
class Score:
    kind: str
    queries: int
    ndcg: dict[int, float]


@dataclass(frozen=True)
class Comparison:
    kind: str
    queries: int
    baseline: float
    candidate: float
    low: float
    high: float
    better: int
    worse: int


def gain(grade: int) -> float:
    return 2.0**grade - 1


def dcg(gains: list[float]) -> float:
    return sum(value / math.log2(position + 2) for position, value in enumerate(gains))


def ndcg(query: Query, ranking: list[str], k: int) -> float:
    grades = {answer.id: answer.grade for answer in query.answers}
    found = [gain(grades[id_]) if id_ in grades else 0.0 for id_ in ranking[:k]]
    ideal = sorted((gain(grade) for grade in grades.values()), reverse=True)[:k]
    return dcg(found) / dcg(ideal)


def rank(pipeline: Pipeline, queries: list[Query], limit: int) -> list[list[str]]:
    rankings = pipeline.search([query.query for query in queries], limit)
    if len(rankings) != len(queries):
        raise BenchError(
            f"the pipeline returned {len(rankings)} rankings for {len(queries)} queries"
        )
    for query, ranking in zip(queries, rankings, strict=True):
        if len(set(ranking)) != len(ranking):
            raise BenchError(f"the pipeline returned an id two times for {query.query!r}")
    return rankings


def query_ndcgs(pipeline: Pipeline, queries: list[Query], k: int) -> npt.NDArray[np.float64]:
    rankings = rank(pipeline, queries, k)
    pairs = zip(queries, rankings, strict=True)
    return np.array([ndcg(query, ranking, k) for query, ranking in pairs])


def groups(queries: list[Query]) -> list[tuple[str, list[int]]]:
    indexes: dict[str, list[int]] = {ALL_KINDS: list(range(len(queries)))}
    for index, query in enumerate(queries):
        indexes.setdefault(query.kind, []).append(index)
    return sorted(indexes.items(), key=lambda item: (item[0] != ALL_KINDS, item[0]))


def evaluate(pipeline: Pipeline, queries: list[Query], ks: list[int]) -> list[Score]:
    rankings = rank(pipeline, queries, max(ks))
    pairs = list(zip(queries, rankings, strict=True))
    values = {k: np.array([ndcg(query, ranking, k) for query, ranking in pairs]) for k in ks}
    return [
        Score(kind, len(indexes), {k: float(values[k][indexes].mean()) for k in ks})
        for kind, indexes in groups(queries)
    ]


def bootstrap_interval(differences: npt.NDArray[np.float64]) -> tuple[float, float]:
    generator = np.random.default_rng(SEED)
    samples = generator.choice(differences, size=(BOOTSTRAP_SAMPLES, len(differences)))
    low, high = np.quantile(samples.mean(axis=1), [(1 - CONFIDENCE) / 2, (1 + CONFIDENCE) / 2])
    return float(low), float(high)


def compare(
    baseline: Pipeline, candidate: Pipeline, queries: list[Query], k: int
) -> list[Comparison]:
    baseline_values = query_ndcgs(baseline, queries, k)
    candidate_values = query_ndcgs(candidate, queries, k)
    comparisons: list[Comparison] = []
    for kind, indexes in groups(queries):
        differences = candidate_values[indexes] - baseline_values[indexes]
        low, high = bootstrap_interval(differences)
        comparisons.append(
            Comparison(
                kind,
                len(indexes),
                float(baseline_values[indexes].mean()),
                float(candidate_values[indexes].mean()),
                low,
                high,
                int((differences > 0).sum()),
                int((differences < 0).sum()),
            )
        )
    return comparisons


def format_scores(scores: list[Score], ks: list[int]) -> str:
    width = max(len(score.kind) for score in scores)
    header = f"{'kind':<{width}}  queries" + "".join(f"  {f'nDCG@{k}':>8}" for k in ks)
    rows = [
        f"{score.kind:<{width}}  {score.queries:>7}"
        + "".join(f"  {score.ndcg[k]:>8.3f}" for k in ks)
        for score in scores
    ]
    return "\n".join([header, *rows])


def format_comparisons(comparisons: list[Comparison], k: int) -> str:
    width = max(len(comparison.kind) for comparison in comparisons)
    interval = f"{CONFIDENCE:.0%} interval"
    header = (
        f"{'kind':<{width}}  queries  {f'A@{k}':>6}  {f'B@{k}':>6}   B - A"
        f"  {interval:>16}  better  worse"
    )
    rows = [
        f"{c.kind:<{width}}  {c.queries:>7}  {c.baseline:>6.3f}  {c.candidate:>6.3f}"
        f"  {c.candidate - c.baseline:>+6.3f}  {f'[{c.low:+.3f}, {c.high:+.3f}]':>16}"
        f"  {c.better:>6}  {c.worse:>5}"
        for c in comparisons
    ]
    return "\n".join([header, *rows])
