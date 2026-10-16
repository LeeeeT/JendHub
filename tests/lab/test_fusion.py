from jendhub.lab.fusion import ReciprocalRankFusion
from tests.lab.batches import FixedPipeline, batch


def test_fusion_ranks_a_result_found_by_both_pipelines_first() -> None:
    fusion = ReciprocalRankFusion(
        [FixedPipeline({"q": ["allow", "List.sort"]}), FixedPipeline({"q": ["List.sort"]})]
    )
    assert fusion.search(batch(["q"]), 10) == [["List.sort", "allow"]]


def test_fusion_keeps_the_order_of_the_first_pipeline_on_ties() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline({"q": ["a"]}), FixedPipeline({"q": ["b"]})])
    assert fusion.search(batch(["q"]), 10) == [["a", "b"]]


def test_fusion_returns_at_most_limit_results() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline({"q": ["a", "b", "c"]})])
    assert fusion.search(batch(["q"]), 2) == [["a", "b"]]


def test_fusion_fuses_each_query_separately() -> None:
    fusion = ReciprocalRankFusion(
        [FixedPipeline({"p": ["a"], "q": ["b"]}), FixedPipeline({"p": ["c"], "q": ["b"]})]
    )
    assert fusion.search(batch(["p", "q"]), 10) == [["a", "c"], ["b"]]


def test_fusion_without_pipelines_gives_empty_rankings() -> None:
    assert ReciprocalRankFusion([]).search(batch(["p", "q"]), 10) == [[], []]
