import math

import pytest

from jendhub.bench import Answer, BenchError, Query
from jendhub.evaluate import (
    Comparison,
    Score,
    compare,
    evaluate,
    format_comparisons,
    format_scores,
    ndcg,
)

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


def test_compare_of_a_pipeline_with_itself_shows_no_change() -> None:
    pipeline = FixedPipeline({MAP.query: ["List.foldr"], ZIP.query: ["List.zip"]})
    [all_kinds, *_] = compare(pipeline, pipeline, [MAP, ZIP], 10)
    assert all_kinds == Comparison("all", 2, all_kinds.baseline, all_kinds.baseline, 0, 0, 0, 0)


def test_compare_counts_the_better_and_the_worse_queries() -> None:
    baseline = FixedPipeline({MAP.query: ["List.foldr"], ZIP.query: ["List.zip"]})
    candidate = FixedPipeline({MAP.query: ["List.map", "List.foldr"], ZIP.query: []})
    comparisons = compare(baseline, candidate, [MAP, ZIP], 10)
    assert [(c.kind, c.better, c.worse) for c in comparisons] == [
        ("all", 1, 1),
        ("name", 0, 1),
        ("phrase", 1, 0),
    ]


def test_compare_gives_an_interval_around_the_mean_change() -> None:
    baseline = FixedPipeline({MAP.query: ["List.foldr"], ZIP.query: []})
    candidate = FixedPipeline({MAP.query: ["List.map", "List.foldr"], ZIP.query: ["List.zip"]})
    [all_kinds, *_] = compare(baseline, candidate, [MAP, ZIP], 10)
    change = all_kinds.candidate - all_kinds.baseline
    assert 0 < all_kinds.low <= change <= all_kinds.high


def test_format_comparisons_prints_one_row_for_each_kind() -> None:
    comparisons = [Comparison("all", 200, 0.5, 0.625, 0.1, 0.15, 30, 12)]
    assert format_comparisons(comparisons, 10).splitlines() == [
        "kind  queries    A@10    B@10   B - A      95% interval  better  worse",
        "all      200   0.500   0.625  +0.125  [+0.100, +0.150]      30     12",
    ]


def test_format_scores_prints_one_row_for_each_kind() -> None:
    scores = [Score("all", 200, {10: 0.5123}), Score("name", 24, {10: 0.25})]
    assert format_scores(scores, [10]).splitlines() == [
        "kind  queries   nDCG@10",
        "all       200     0.512",
        "name       24     0.250",
    ]
