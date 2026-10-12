import math
from dataclasses import dataclass
from typing import Protocol

from jendhub.bench import BenchError, Query

ALL_KINDS = "all"


class Pipeline(Protocol):
    def search(self, queries: list[str], limit: int) -> list[list[str]]: ...


@dataclass(frozen=True)
class Score:
    kind: str
    queries: int
    ndcg: dict[int, float]


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


def mean_ndcg(queries: list[Query], rankings: list[list[str]], k: int) -> float:
    pairs = zip(queries, rankings, strict=True)
    return sum(ndcg(query, ranking, k) for query, ranking in pairs) / len(queries)


def evaluate(pipeline: Pipeline, queries: list[Query], ks: list[int]) -> list[Score]:
    rankings = rank(pipeline, queries, max(ks))
    groups: dict[str, list[int]] = {ALL_KINDS: list(range(len(queries)))}
    for index, query in enumerate(queries):
        groups.setdefault(query.kind, []).append(index)
    scores: list[Score] = []
    for kind, indexes in sorted(groups.items(), key=lambda item: (item[0] != ALL_KINDS, item[0])):
        group = [queries[i] for i in indexes]
        group_rankings = [rankings[i] for i in indexes]
        ndcgs = {k: mean_ndcg(group, group_rankings, k) for k in ks}
        scores.append(Score(kind, len(indexes), ndcgs))
    return scores


def format_scores(scores: list[Score], ks: list[int]) -> str:
    width = max(len(score.kind) for score in scores)
    header = f"{'kind':<{width}}  queries" + "".join(f"  {f'nDCG@{k}':>8}" for k in ks)
    rows = [
        f"{score.kind:<{width}}  {score.queries:>7}"
        + "".join(f"  {score.ndcg[k]:>8.3f}" for k in ks)
        for score in scores
    ]
    return "\n".join([header, *rows])
