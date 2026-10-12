from jendhub.rerank import LinearRerank, MaxNormalized


class FixedPipeline:
    def __init__(self, ranking: list[str]) -> None:
        self.ranking = ranking

    def search(self, query: str, limit: int) -> list[str]:
        return self.ranking[:limit]


class FixedScorer:
    def __init__(self, scores: dict[str, float]) -> None:
        self.table = scores

    def scores(self, query: str, ids: list[str]) -> list[float]:
        return [self.table.get(id_, 0.0) for id_ in ids]


LEXICAL = FixedScorer({"allow": 8.0, "List.sort": 6.0, "List.sort.runs": 4.0})
NAMES = FixedScorer({"List.sort": 1.0, "List.sort.runs": 0.4})
CANDIDATES = FixedPipeline(["allow", "List.sort", "List.sort.runs"])


def test_max_normalized_divides_by_the_best_score() -> None:
    assert MaxNormalized(LEXICAL).scores("q", ["allow", "List.sort"]) == [1.0, 0.75]


def test_max_normalized_gives_zero_when_no_candidate_scores() -> None:
    assert MaxNormalized(FixedScorer({})).scores("q", ["a", "b"]) == [0.0, 0.0]


def test_rerank_orders_the_candidates_by_the_weighted_sum() -> None:
    rerank = LinearRerank(CANDIDATES, [(1.0, MaxNormalized(LEXICAL)), (1.0, NAMES)])
    assert rerank.search("List.sort", 10) == ["List.sort", "allow", "List.sort.runs"]


def test_rerank_keeps_the_candidate_order_without_features() -> None:
    assert LinearRerank(CANDIDATES, []).search("q", 2) == ["allow", "List.sort"]
