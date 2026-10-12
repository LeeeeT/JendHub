import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from jendhub.bench import BenchError
from jendhub.judgments import Assessment

ALL_KINDS = "all"
BOOTSTRAP_SAMPLES = 10_000
CONFIDENCE = 0.95
SEED = 0

type Values = npt.NDArray[np.float64]


class Pipeline(Protocol):
    def search(self, queries: list[str], limit: int) -> list[list[str]]: ...


@dataclass(frozen=True)
class Score:
    kind: str
    queries: int
    ndcg: dict[int, float]
    judged: dict[int, float]


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
    baseline_judged: float
    candidate_judged: float


def gain(grade: int) -> float:
    return 2.0**grade - 1


def dcg(gains: list[float]) -> float:
    return sum(value / math.log2(position + 2) for position, value in enumerate(gains))


def ndcg(assessment: Assessment, ranking: list[str], k: int) -> float:
    grades = assessment.grades
    found = [gain(grades.get(id_, 0)) for id_ in ranking[:k]]
    ideal = sorted((gain(grade) for grade in grades.values()), reverse=True)[:k]
    return dcg(found) / dcg(ideal)


def judged_count(assessment: Assessment, ranking: list[str], k: int) -> int:
    return sum(id_ in assessment.grades for id_ in ranking[:k])


def rank(pipeline: Pipeline, assessments: list[Assessment], limit: int) -> list[list[str]]:
    rankings = pipeline.search([assessment.query for assessment in assessments], limit)
    if len(rankings) != len(assessments):
        raise BenchError(
            f"the pipeline returned {len(rankings)} rankings for {len(assessments)} queries"
        )
    for assessment, ranking in zip(assessments, rankings, strict=True):
        if len(set(ranking)) != len(ranking):
            raise BenchError(f"the pipeline returned an id two times for {assessment.query!r}")
    return rankings


@dataclass(frozen=True)
class Measures:
    ndcg: Values
    judged: Values
    results: Values

    def judged_share(self, indexes: list[int]) -> float:
        results = self.results[indexes].sum()
        return float(self.judged[indexes].sum() / results) if results else 1.0


def measure(assessments: list[Assessment], rankings: list[list[str]], k: int) -> Measures:
    pairs = list(zip(assessments, rankings, strict=True))
    return Measures(
        np.array([ndcg(assessment, ranking, k) for assessment, ranking in pairs]),
        np.array([judged_count(assessment, ranking, k) for assessment, ranking in pairs]),
        np.array([len(ranking[:k]) for _, ranking in pairs], dtype=np.float64),
    )


def groups(assessments: list[Assessment]) -> list[tuple[str, list[int]]]:
    indexes: dict[str, list[int]] = {ALL_KINDS: list(range(len(assessments)))}
    for index, assessment in enumerate(assessments):
        indexes.setdefault(assessment.kind, []).append(index)
    return sorted(indexes.items(), key=lambda item: (item[0] != ALL_KINDS, item[0]))


def evaluate(pipeline: Pipeline, assessments: list[Assessment], ks: list[int]) -> list[Score]:
    rankings = rank(pipeline, assessments, max(ks))
    measures = {k: measure(assessments, rankings, k) for k in ks}
    return [
        Score(
            kind,
            len(indexes),
            {k: float(measures[k].ndcg[indexes].mean()) for k in ks},
            {k: measures[k].judged_share(indexes) for k in ks},
        )
        for kind, indexes in groups(assessments)
    ]


def bootstrap_interval(differences: Values) -> tuple[float, float]:
    generator = np.random.default_rng(SEED)
    samples = generator.choice(differences, size=(BOOTSTRAP_SAMPLES, len(differences)))
    low, high = np.quantile(samples.mean(axis=1), [(1 - CONFIDENCE) / 2, (1 + CONFIDENCE) / 2])
    return float(low), float(high)


def compare(
    baseline: Pipeline, candidate: Pipeline, assessments: list[Assessment], k: int
) -> list[Comparison]:
    old = measure(assessments, rank(baseline, assessments, k), k)
    new = measure(assessments, rank(candidate, assessments, k), k)
    comparisons: list[Comparison] = []
    for kind, indexes in groups(assessments):
        differences = new.ndcg[indexes] - old.ndcg[indexes]
        low, high = bootstrap_interval(differences)
        comparisons.append(
            Comparison(
                kind,
                len(indexes),
                float(old.ndcg[indexes].mean()),
                float(new.ndcg[indexes].mean()),
                low,
                high,
                int((differences > 0).sum()),
                int((differences < 0).sum()),
                old.judged_share(indexes),
                new.judged_share(indexes),
            )
        )
    return comparisons


def format_scores(scores: list[Score], ks: list[int]) -> str:
    width = max(len(score.kind) for score in scores)
    header = f"{'kind':<{width}}  queries" + "".join(
        f"  {f'nDCG@{k}':>8}  {f'judged@{k}':>9}" for k in ks
    )
    rows = [
        f"{score.kind:<{width}}  {score.queries:>7}"
        + "".join(f"  {score.ndcg[k]:>8.3f}  {score.judged[k]:>9.3f}" for k in ks)
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
    first = comparisons[0]
    judged = f"judged@{k}: A {first.baseline_judged:.3f}, B {first.candidate_judged:.3f}"
    return "\n".join([header, *rows, judged])
