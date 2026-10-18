from jendhub.lab.bm25 import Bm25
from tests.lab.batches import batch

DOCUMENTS = [
    ("List.map", "def List.map(f, xs: List) -> List: apply f to each element"),
    ("List.zip", "def List.zip(xs: List, ys: List) -> List: pair the elements"),
    ("Nat.add", "def Nat.add(a: Nat, b: Nat) -> Nat: add two naturals"),
]


def test_search_ranks_the_best_match_first() -> None:
    assert Bm25(DOCUMENTS).search(batch(["list map"]), 10) == [["List.map", "List.zip"]]


def test_search_gives_one_ranking_for_each_query() -> None:
    assert Bm25(DOCUMENTS).search(batch(["add", "zip"]), 10) == [["Nat.add"], ["List.zip"]]


def test_search_returns_at_most_limit_results() -> None:
    assert Bm25(DOCUMENTS).search(batch(["list map"]), 1) == [["List.map"]]


def test_search_omits_documents_without_a_query_term() -> None:
    assert Bm25(DOCUMENTS).search(batch(["socket"]), 10) == [[]]


def test_search_breaks_ties_by_document_order() -> None:
    assert Bm25([("a", "same"), ("b", "same")]).search(batch(["same"]), 10) == [["a", "b"]]


def test_an_empty_index_returns_no_results() -> None:
    assert Bm25([]).search(batch(["map"]), 10) == [[]]


def test_scores_match_the_search_order() -> None:
    index = Bm25(DOCUMENTS)
    [scores] = index.scores(batch(["list map"]), [["Nat.add", "List.zip", "List.map"]])
    assert scores[0] == 0.0
    assert scores[2] > scores[1] > 0.0
