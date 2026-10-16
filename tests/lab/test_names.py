from jendhub.lab.names import NameMatch
from jendhub.search.names import overlap
from tests.lab.batches import batch

NAMES = NameMatch(
    [
        ("allow", "allow"),
        ("shuffle.run", "shuffle.run"),
        ("shuffle", "shuffle"),
        ("List.sort", "List.sort"),
        ("List.sort.runs", "List.sort.runs"),
    ]
)


def test_overlap_is_the_share_of_shared_tokens() -> None:
    assert overlap({"shuffle"}, {"shuffle.run", "shuffle", "run"}) == 1 / 3


def test_search_ranks_the_exact_name_first() -> None:
    assert NAMES.search(batch(["List.sort"]), 10) == [["List.sort"]]


def test_search_gives_one_ranking_for_each_query() -> None:
    assert NAMES.search(batch(["allow", "shuffle"]), 10) == [["allow"], ["shuffle"]]


def test_search_omits_names_with_a_small_overlap() -> None:
    assert NAMES.search(batch(["shuffle"]), 10) == [["shuffle"]]


def test_search_keeps_names_with_a_large_overlap() -> None:
    assert NAMES.search(batch(["sort list"]), 10) == [["List.sort", "List.sort.runs"]]


def test_search_returns_nothing_for_unrelated_words() -> None:
    assert NAMES.search(batch(["socket"]), 10) == [[]]


def test_scores_gives_the_overlap_of_each_name() -> None:
    scores = NAMES.scores(
        batch(["shuffle", "allow"]), [["shuffle", "shuffle.run", "allow"], ["allow"]]
    )
    assert scores == [[1.0, 1 / 3, 0.0], [1.0]]
