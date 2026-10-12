from jendhub.fusion import ReciprocalRankFusion


class FixedPipeline:
    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings

    def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return [self.rankings.get(query, [])[:limit] for query in queries]


def test_fusion_ranks_a_result_found_by_both_pipelines_first() -> None:
    fusion = ReciprocalRankFusion(
        [FixedPipeline({"q": ["allow", "List.sort"]}), FixedPipeline({"q": ["List.sort"]})]
    )
    assert fusion.search(["q"], 10) == [["List.sort", "allow"]]


def test_fusion_keeps_the_order_of_the_first_pipeline_on_ties() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline({"q": ["a"]}), FixedPipeline({"q": ["b"]})])
    assert fusion.search(["q"], 10) == [["a", "b"]]


def test_fusion_returns_at_most_limit_results() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline({"q": ["a", "b", "c"]})])
    assert fusion.search(["q"], 2) == [["a", "b"]]


def test_fusion_fuses_each_query_separately() -> None:
    fusion = ReciprocalRankFusion(
        [FixedPipeline({"p": ["a"], "q": ["b"]}), FixedPipeline({"p": ["c"], "q": ["b"]})]
    )
    assert fusion.search(["p", "q"], 10) == [["a", "c"], ["b"]]


def test_fusion_without_pipelines_gives_empty_rankings() -> None:
    assert ReciprocalRankFusion([]).search(["p", "q"], 10) == [[], []]
