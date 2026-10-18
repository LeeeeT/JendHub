import math

import pytest

from jendhub.bench.evaluate import (
    Comparison,
    Score,
    compare,
    evaluate,
    ndcg,
)
from jendhub.bench.judgments import Assessment
from jendhub.bench.queries import BenchError

MAP = Assessment("map a list", "phrase", {"List.map": 3, "List.foldr": 1, "List.zip": 0})
ZIP = Assessment("zip", "name", {"List.zip": 3})


class FixedPipeline:
    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings

    async def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return [self.rankings[query][:limit] for query in queries]


class MissingRankingPipeline:
    async def search(self, queries: list[str], limit: int) -> list[list[str]]:
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


async def test_evaluate_scores_all_queries_and_each_kind() -> None:
    pipeline = FixedPipeline({MAP.text: ["List.map", "List.foldr"], ZIP.text: ["List.map"]})
    assert await evaluate(pipeline, [MAP, ZIP], [1, 10]) == [
        Score("all", 2, {1: 0.5, 10: 0.5}, {1: 0.5, 10: 2 / 3}),
        Score("name", 1, {1: 0.0, 10: 0.0}, {1: 0.0, 10: 0.0}),
        Score("phrase", 1, {1: 1.0, 10: 1.0}, {1: 1.0, 10: 1.0}),
    ]


async def test_evaluate_counts_a_grade_zero_as_judged() -> None:
    [all_kinds, *_] = await evaluate(FixedPipeline({MAP.text: ["List.zip"]}), [MAP], [10])
    assert all_kinds.ndcg[10] == 0.0
    assert all_kinds.judged[10] == 1.0


async def test_evaluate_counts_an_empty_ranking_as_judged() -> None:
    [all_kinds, *_] = await evaluate(FixedPipeline({ZIP.text: []}), [ZIP], [10])
    assert all_kinds.judged[10] == 1.0


async def test_evaluate_refuses_a_ranking_with_a_repeated_id() -> None:
    pipeline = FixedPipeline({ZIP.text: ["List.zip", "List.zip"]})
    with pytest.raises(BenchError):
        await evaluate(pipeline, [ZIP], [10])


async def test_evaluate_refuses_a_missing_ranking() -> None:
    with pytest.raises(BenchError):
        await evaluate(MissingRankingPipeline(), [ZIP], [10])


async def test_compare_of_a_pipeline_with_itself_shows_no_change() -> None:
    pipeline = FixedPipeline({MAP.text: ["List.foldr"], ZIP.text: ["List.zip"]})
    [all_kinds, *_] = await compare(pipeline, pipeline, [MAP, ZIP], 10)
    assert all_kinds == Comparison(
        "all", 2, all_kinds.baseline, all_kinds.baseline, 0, 0, 0, 0, 1.0, 1.0
    )


async def test_compare_counts_the_better_and_the_worse_queries() -> None:
    baseline = FixedPipeline({MAP.text: ["List.foldr"], ZIP.text: ["List.zip"]})
    candidate = FixedPipeline({MAP.text: ["List.map", "List.foldr"], ZIP.text: []})
    comparisons = await compare(baseline, candidate, [MAP, ZIP], 10)
    assert [(c.kind, c.better, c.worse) for c in comparisons] == [
        ("all", 1, 1),
        ("name", 0, 1),
        ("phrase", 1, 0),
    ]


async def test_compare_gives_an_interval_around_the_mean_change() -> None:
    baseline = FixedPipeline({MAP.text: ["List.foldr"], ZIP.text: []})
    candidate = FixedPipeline({MAP.text: ["List.map", "List.foldr"], ZIP.text: ["List.zip"]})
    [all_kinds, *_] = await compare(baseline, candidate, [MAP, ZIP], 10)
    change = all_kinds.candidate - all_kinds.baseline
    assert 0 < all_kinds.low <= change <= all_kinds.high


async def test_compare_gives_the_judged_share_of_each_pipeline() -> None:
    baseline = FixedPipeline({ZIP.text: ["List.zip"]})
    candidate = FixedPipeline({ZIP.text: ["List.zip", "List.map"]})
    [all_kinds, *_] = await compare(baseline, candidate, [ZIP], 10)
    assert (all_kinds.baseline_judged, all_kinds.candidate_judged) == (1.0, 0.5)
