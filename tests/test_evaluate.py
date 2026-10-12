import math

import pytest

from jendhub.bench import Answer, BenchError, Query
from jendhub.evaluate import Score, evaluate, format_scores, ndcg

MAP = Query("map a list", "phrase", [Answer("List.map", 3), Answer("List.foldr", 1)])
ZIP = Query("zip", "name", [Answer("List.zip", 3)])


class FixedPipeline:
    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings

    def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return [self.rankings[query][:limit] for query in queries]


class MissingRankingPipeline:
    def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return []


def test_ndcg_is_one_for_the_ideal_ranking() -> None:
    assert ndcg(MAP, ["List.map", "List.foldr", "List.zip"], 10) == 1.0


def test_ndcg_uses_exponential_gain_and_a_log_discount() -> None:
    ideal = 7 + 1 / math.log2(3)
    assert ndcg(MAP, ["List.foldr", "List.map"], 10) == pytest.approx(
        (1 + 7 / math.log2(3)) / ideal
    )


def test_ndcg_counts_only_the_first_k_results() -> None:
    assert ndcg(MAP, ["List.zip", "List.map"], 1) == 0.0


def test_ndcg_limits_the_ideal_ranking_to_k() -> None:
    assert ndcg(MAP, ["List.map"], 1) == 1.0


def test_evaluate_scores_all_queries_and_each_kind() -> None:
    pipeline = FixedPipeline({MAP.query: ["List.map", "List.foldr"], ZIP.query: ["List.map"]})
    assert evaluate(pipeline, [MAP, ZIP], [1, 10]) == [
        Score("all", 2, {1: 0.5, 10: 0.5}),
        Score("name", 1, {1: 0.0, 10: 0.0}),
        Score("phrase", 1, {1: 1.0, 10: 1.0}),
    ]


def test_evaluate_refuses_a_ranking_with_a_repeated_id() -> None:
    pipeline = FixedPipeline({ZIP.query: ["List.zip", "List.zip"]})
    with pytest.raises(BenchError):
        evaluate(pipeline, [ZIP], [10])


def test_evaluate_refuses_a_missing_ranking() -> None:
    with pytest.raises(BenchError):
        evaluate(MissingRankingPipeline(), [ZIP], [10])


def test_format_scores_prints_one_row_for_each_kind() -> None:
    scores = [Score("all", 200, {10: 0.5123}), Score("name", 24, {10: 0.25})]
    assert format_scores(scores, [10]).splitlines() == [
        "kind  queries   nDCG@10",
        "all       200     0.512",
        "name       24     0.250",
    ]
