import hashlib
from collections.abc import Sequence
from typing import Protocol

import lightgbm as lgb
import numpy as np

from jendhub.embedding.model import Vectors
from jendhub.lab.features import Features
from jendhub.lab.pipeline import QueryBatch
from jendhub.search.ranking import Matrix, Model

FOLDS = 5
ROUNDS = 300
PARAMETERS = {
    "objective": "lambdarank",
    "lambdarank_truncation_level": 20,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 0,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
}


class GradedQuery(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def grades(self) -> dict[str, int]: ...


type Example = tuple[list[str], Matrix, dict[str, int]]


def fold(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4]) % FOLDS


def examples(features: Features, queries: Sequence[GradedQuery], vectors: Vectors) -> list[Example]:
    return [
        (*features.matrix(query.text, vector), query.grades)
        for query, vector in zip(queries, vectors, strict=True)
    ]


def fit(found: list[Example]) -> lgb.Booster:
    data = lgb.Dataset(
        np.vstack([matrix for _, matrix, _ in found]),
        [grades.get(id_, 0) for ids, _, grades in found for id_ in ids],
        group=[len(ids) for ids, _, _ in found],
    )
    booster = lgb.Booster(PARAMETERS, data)
    for _ in range(ROUNDS):
        booster.update()
    return booster


def train_folds(
    features: Features, queries: Sequence[GradedQuery], vectors: Vectors
) -> list[lgb.Booster]:
    found = examples(features, queries, vectors)
    folds = [fold(query.text) for query in queries]
    return [
        fit([example for example, own in zip(found, folds, strict=True) if own != held_out])
        for held_out in range(FOLDS)
    ]


def train_all(features: Features, queries: Sequence[GradedQuery], vectors: Vectors) -> lgb.Booster:
    return fit(examples(features, queries, vectors))


class LearnedRank:
    def __init__(self, features: Features, models: list[Model], instruction: str) -> None:
        self.features = features
        self.models = models
        self.instruction = instruction

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset({self.instruction})

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]:
        found: list[list[str]] = []
        for text, vector in zip(batch.texts, batch.vectors[self.instruction], strict=True):
            ids, matrix = self.features.matrix(text, vector)
            scores = self.models[fold(text)].predict(matrix)
            order = np.argsort(-scores, kind="stable")[:limit]
            found.append([ids[index] for index in order])
        return found
