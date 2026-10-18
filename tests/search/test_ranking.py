import math
from dataclasses import replace

import numpy as np
import pytest

from jendhub.search.ranking import FEATURES, Signals, feature_matrix, jaccard, load_model, singular


def signals(name: str, code: float, **changes: object) -> Signals:
    return replace(
        Signals(
            name,
            "def",
            base=True,
            length=10,
            signature=None,
            documented=True,
            helper=False,
            users=0,
            external=False,
            module="Base",
            code=code,
            name_similarity=code,
            module_best=code,
            package_best=code,
            parent=None,
            children=[],
        ),
        **changes,
    )


def column(matrix: np.ndarray, name: str) -> list[float]:
    return matrix[:, FEATURES.index(name)].tolist()


def test_singular_removes_plural_endings_of_long_words() -> None:
    assert [singular(word) for word in ("entries", "matches", "lists", "bus")] == [
        "entry",
        "match",
        "list",
        "bus",
    ]


def test_feature_matrix_has_one_column_for_each_feature() -> None:
    matrix = feature_matrix("map a list", [signals("List.map", 0.9), signals("Nat.add", 0.1)])
    assert matrix.shape == (2, len(FEATURES))
    assert column(matrix, "code") == pytest.approx([0.9, 0.1])


def test_feature_matrix_measures_relative_values_inside_the_query() -> None:
    matrix = feature_matrix("map a list", [signals("List.map", 0.9), signals("Nat.add", 0.5)])
    assert column(matrix, "name_gap") == pytest.approx([0.0, -0.4])
    assert column(matrix, "code_z") == pytest.approx([1.0, -1.0], abs=1e-4)


def test_feature_matrix_compares_with_the_parent_and_the_children() -> None:
    found = signals("List.map", 0.8, parent=0.6, children=[0.7, 0.9])
    lonely = signals("Nat.add", 0.5)
    matrix = feature_matrix("map", [found, lonely])
    assert column(matrix, "parent_gap")[0] == pytest.approx(-0.2)
    assert column(matrix, "children_gap")[0] == pytest.approx(0.1)
    assert column(matrix, "log_children")[0] == pytest.approx(math.log1p(2))
    assert math.isnan(column(matrix, "parent_gap")[1])
    assert math.isnan(column(matrix, "children_gap")[1])


def test_feature_matrix_measures_the_words_that_the_query_and_the_name_share() -> None:
    matrix = feature_matrix("map lists", [signals("List.map", 0.9), signals("Nat.add", 0.5)])
    assert column(matrix, "last_coverage") == pytest.approx([1.0, 0.0])
    assert column(matrix, "query_coverage") == pytest.approx([1.0, 0.0])
    assert column(matrix, "module_count") == [2.0, 2.0]


def test_the_shipped_model_scores_each_candidate() -> None:
    matrix = feature_matrix("map a list", [signals("List.map", 0.9), signals("Nat.add", 0.1)])
    assert load_model().predict(matrix).shape == (2,)


def test_jaccard_is_the_share_of_shared_tokens() -> None:
    assert jaccard(frozenset({"shuffle"}), frozenset({"shuffle.run", "shuffle", "run"})) == 1 / 3
    assert jaccard(frozenset(), frozenset()) == 0.0
