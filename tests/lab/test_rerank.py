from jendhub.lab.rerank import LinearRerank, MaxNormalized
from tests.lab.batches import FixedPipeline, FixedScorer, batch

LEXICAL = FixedScorer({"allow": 8.0, "List.sort": 6.0, "List.sort.runs": 4.0})
NAMES = FixedScorer({"List.sort": 1.0, "List.sort.runs": 0.4})
CANDIDATES = FixedPipeline(
    {query: ["allow", "List.sort", "List.sort.runs"] for query in "pq"}
    | {"List.sort": ["allow", "List.sort", "List.sort.runs"]}
)


def test_max_normalized_divides_by_the_best_score_of_each_query() -> None:
    scores = MaxNormalized(LEXICAL).scores(
        batch(["p", "q"]), [["allow", "List.sort"], ["List.sort"]]
    )
    assert scores == [[1.0, 0.75], [1.0]]


def test_max_normalized_gives_zero_when_no_candidate_scores() -> None:
    assert MaxNormalized(FixedScorer({})).scores(batch(["q"]), [["a", "b"]]) == [[0.0, 0.0]]


def test_rerank_orders_the_candidates_by_the_weighted_sum() -> None:
    rerank = LinearRerank(CANDIDATES, [(1.0, MaxNormalized(LEXICAL)), (1.0, NAMES)])
    assert rerank.search(batch(["List.sort"]), 10) == [["List.sort", "allow", "List.sort.runs"]]


def test_rerank_gives_one_ranking_for_each_query() -> None:
    rerank = LinearRerank(CANDIDATES, [(1.0, NAMES)])
    assert len(rerank.search(batch(["p", "q"]), 10)) == 2


def test_rerank_keeps_the_candidate_order_without_features() -> None:
    assert LinearRerank(CANDIDATES, []).search(batch(["q"]), 2) == [["allow", "List.sort"]]
