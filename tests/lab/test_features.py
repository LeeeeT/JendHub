import math

import numpy as np
import pytest

from jendhub.corpus.definitions import BASE, Definition, Origin
from jendhub.hub.api import Release
from jendhub.lab.facts import Facts
from jendhub.lab.features import Features
from jendhub.search.ranking import FEATURES
from jendhub.vectors.index import VectorConfig

CONFIG = VectorConfig(3, "none")
PACKAGE = Origin(Release("pkg", "1.0.0"), "mod")
CODE = {
    "List": [0.8, 0.6, 0.0],
    "List.map": [1.0, 0.0, 0.0],
    "List.map.go": [0.6, 0.8, 0.0],
    "Nat.add": [0.0, 0.0, 1.0],
    "pkg@1.0.0/mod:sort": [0.0, 0.6, 0.8],
}


def definition(id_: str, origin: Origin) -> Definition:
    name = id_.rsplit(":", 1)[-1]
    kind = "type" if name == "List" else "def"
    return Definition(id_, kind, name, origin, f"def {name}():", f"def {name}():", 0, 1)


def features() -> Features:
    definitions = [definition(id_, PACKAGE if ":" in id_ else BASE) for id_ in CODE]
    facts = {
        found.id: Facts(None, documented=True, helper=False, users=1, external=False, reached=[])
        for found in definitions
    }
    code = np.array(list(CODE.values()), dtype=np.float32)
    return Features(definitions, facts, code, code, CONFIG)


def column(matrix: np.ndarray, name: str) -> list[float]:
    return matrix[:, FEATURES.index(name)].tolist()


def test_matrix_ranks_candidates_by_code_similarity() -> None:
    ids, matrix = features().matrix("map a list", np.array([1.0, 0.0, 0.0], dtype=np.float32))
    assert ids == ["List.map", "List", "List.map.go", "Nat.add", "pkg@1.0.0/mod:sort"]
    assert matrix.shape == (len(CODE), len(FEATURES))
    assert column(matrix, "code") == pytest.approx([1.0, 0.8, 0.6, 0.0, 0.0])


def test_matrix_compares_a_definition_with_its_parent_and_children() -> None:
    _, matrix = features().matrix("map a list", np.array([1.0, 0.0, 0.0], dtype=np.float32))
    parents = column(matrix, "parent_gap")
    children = column(matrix, "children_gap")
    assert parents[0] == pytest.approx(-0.2)
    assert children[0] == pytest.approx(-0.4)
    assert math.isnan(parents[3])
    assert math.isnan(children[3])


def test_matrix_measures_the_words_that_the_query_and_the_name_share() -> None:
    _, matrix = features().matrix("map lists", np.array([1.0, 0.0, 0.0], dtype=np.float32))
    assert column(matrix, "last_coverage")[0] == pytest.approx(1.0)
    assert column(matrix, "query_coverage")[0] == pytest.approx(1.0)
    assert column(matrix, "query_coverage")[3] == pytest.approx(0.0)


def test_matrix_compares_each_candidate_with_the_best_of_its_package() -> None:
    _, matrix = features().matrix("sort", np.array([0.0, 0.6, 0.8], dtype=np.float32))
    assert column(matrix, "package_gap")[0] == pytest.approx(0.0)
    assert column(matrix, "query_best") == pytest.approx([1.0] * len(CODE))
