import math
from pathlib import Path

from jend.benchmark import (
    Judgment,
    Query,
    auc,
    bootstrap,
    dump,
    load,
    ndcg,
    recall,
    reciprocal_rank,
)


def test_ndcg_is_one_for_the_ideal_order_and_lower_for_a_swap() -> None:
    grades = {"a": 3, "b": 2, "c": 1}

    assert ndcg(["a", "b", "c"], grades, 10) == 1.0
    assert ndcg(["b", "a", "c"], grades, 10) < 1.0
    assert ndcg(["x", "y"], grades, 10) == 0.0


def test_ndcg_ideal_uses_all_judgments_not_only_the_ranked_ones() -> None:
    grades = {"a": 3, "b": 3}

    assert math.isclose(ndcg(["a"], grades, 10), 7 / (7 + 7 / math.log2(3)))


def test_ndcg_cuts_both_lists_at_the_cutoff() -> None:
    grades = {"a": 3, "b": 3}

    assert ndcg(["x", "a", "b"], grades, 1) == 0.0
    assert ndcg(["a", "x", "b"], grades, 1) == 1.0


def test_reciprocal_rank_and_recall() -> None:
    assert reciprocal_rank(["x", "a", "b"], {"a", "b"}) == 0.5
    assert reciprocal_rank(["x"], {"a"}) == 0.0
    assert recall(["a", "x", "b"], {"a", "b"}, 2) == 0.5
    assert recall(["a"], set(), 2) == 0.0


def test_auc_counts_ties_as_half() -> None:
    assert auc([0.9, 0.8], [0.1]) == 1.0
    assert auc([0.5], [0.5]) == 0.5
    assert auc([0.2], [0.4, 0.1]) == 0.5


def test_bootstrap_is_deterministic_and_contains_the_mean() -> None:
    deltas = [0.1, -0.05, 0.2, 0.0, 0.15]

    low, high = bootstrap(deltas)

    assert (low, high) == bootstrap(deltas)
    assert low <= sum(deltas) / len(deltas) <= high


def test_dump_writes_a_file_that_load_reads_back(tmp_path: Path) -> None:
    queries = (
        Query(
            id="q001",
            query='parse "json"',
            style="task",
            topic="json",
            judgments=(
                Judgment(key="b", grade=1, definition="p/f.bend:parse.go", note="loop"),
                Judgment(key="a", grade=3, definition="p/f.bend:parse", note="parses"),
            ),
        ),
    )
    path = tmp_path / "benchmark.json"

    path.write_text(dump(queries), encoding="utf-8")

    loaded = load(path)
    assert loaded[0].query == queries[0].query
    assert [judgment.key for judgment in loaded[0].judgments] == ["a", "b"]
    assert loaded[0].answers() == {"a"}
