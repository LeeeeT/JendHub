from dataclasses import dataclass

import numpy as np

from jendhub.lab.features import FEATURES, Matrix
from jendhub.lab.learned import FOLDS, LearnedRank, fold, train
from jendhub.lab.pipeline import QueryBatch
from tests.lab.test_features import features

INSTRUCTION = "Find the definition"


@dataclass(frozen=True)
class Graded:
    text: str
    grades: dict[str, int]


class ReversedCode:
    def predict(self, data: Matrix) -> Matrix:
        return -data[:, FEATURES.index("code")]


def test_fold_is_stable_and_within_the_folds() -> None:
    folds = [fold(f"query {number}") for number in range(100)]
    assert folds == [fold(f"query {number}") for number in range(100)]
    assert set(folds) == set(range(FOLDS))


def test_search_orders_the_candidates_by_the_model_of_the_query_fold() -> None:
    ranker = LearnedRank(features(), [ReversedCode()] * FOLDS, INSTRUCTION)
    vectors = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    found = ranker.search(QueryBatch(["map a list"], {INSTRUCTION: vectors}), 2)
    assert found == [["Nat.add", "pkg@1.0.0/mod:sort"]]


def test_train_gives_one_model_for_each_fold() -> None:
    texts = [f"query {number}" for number in range(20)]
    queries = [Graded(text, {"List.map": 3, "List": 1}) for text in texts]
    vectors = np.tile(np.array([1.0, 0.0, 0.0], dtype=np.float32), (len(texts), 1))
    models = train(features(), queries, vectors)
    assert len(models) == FOLDS
    assert models[0].predict(features().matrix("q", vectors[0])[1]).shape == (5,)
