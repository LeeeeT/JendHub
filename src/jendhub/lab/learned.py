import hashlib
from collections.abc import Sequence
from typing import Protocol, cast

import lightgbm as lgb
import numpy as np

from jendhub.embedding.model import Vectors
from jendhub.lab.features import Features, Matrix
from jendhub.lab.pipeline import QueryBatch

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


class Model(Protocol):
    def predict(self, data: Matrix) -> Matrix: ...


class Trees:
    def __init__(self, booster: lgb.Booster) -> None:
        self.booster = booster

    def predict(self, data: Matrix) -> Matrix:
        scores = self.booster.predict(data)  # pyright: ignore[reportUnknownMemberType]
        return np.asarray(cast("Matrix", scores), dtype=np.float64)


def fold(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4]) % FOLDS


def fit(data: lgb.Dataset) -> Trees:
    booster = lgb.Booster(PARAMETERS, data)
    for _ in range(ROUNDS):
        booster.update()
    return Trees(booster)


def train(features: Features, queries: Sequence[GradedQuery], vectors: Vectors) -> list[Model]:
    examples = [
        (fold(query.text), *features.matrix(query.text, vector), query.grades)
        for query, vector in zip(queries, vectors, strict=True)
    ]
    models: list[Model] = []
    for held_out in range(FOLDS):
        kept = [example for example in examples if example[0] != held_out]
        data = lgb.Dataset(
            np.vstack([matrix for _, _, matrix, _ in kept]),
            [grades.get(id_, 0) for _, ids, _, grades in kept for id_ in ids],
            group=[len(ids) for _, ids, _, _ in kept],
        )
        models.append(fit(data))
    return models


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
