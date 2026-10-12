from jendhub.fusion import ReciprocalRankFusion


class FixedPipeline:
    def __init__(self, ranking: list[str]) -> None:
        self.ranking = ranking

    def search(self, query: str, limit: int) -> list[str]:
        return self.ranking[:limit]


def test_fusion_ranks_a_result_found_by_both_pipelines_first() -> None:
    fusion = ReciprocalRankFusion(
        [FixedPipeline(["allow", "List.sort"]), FixedPipeline(["List.sort"])]
    )
    assert fusion.search("List.sort", 10) == ["List.sort", "allow"]


def test_fusion_keeps_the_order_of_the_first_pipeline_on_ties() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline(["a"]), FixedPipeline(["b"])])
    assert fusion.search("x", 10) == ["a", "b"]


def test_fusion_returns_at_most_limit_results() -> None:
    fusion = ReciprocalRankFusion([FixedPipeline(["a", "b", "c"])])
    assert fusion.search("x", 2) == ["a", "b"]
