import argparse
import asyncio
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import numpy.typing as npt

from jend import embed, openrouter
from jend.benchmark import ANSWER, Query, load
from jend.features import NAMES, Candidates, Featurizer, Matrix, normalize, positions
from jend.index import Index
from jend.index import load as load_index

MODEL_PATH = Path(__file__).with_name("ranker.json")
FOLDS = 5
ROUNDS = 200
CALIBRATION_DEPTH = 20
PARAMETERS: dict[str, object] = {
    "objective": "lambdarank",
    "label_gain": [0, 1, 3, 7],
    "lambdarank_truncation_level": 20,
    "num_leaves": 7,
    "learning_rate": 0.05,
    "min_data_in_leaf": 20,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "seed": 0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbose": -1,
}

Scores = npt.NDArray[np.float64]
Grades = npt.NDArray[np.int32]


@dataclass(frozen=True)
class Example:
    features: Matrix
    grades: Grades


@dataclass(frozen=True)
class Ranker:
    booster: lgb.Booster
    slope: float
    intercept: float

    def scores(self, features: Matrix) -> Scores:
        return np.asarray(self.booster.predict(features), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType]

    def probabilities(self, scores: Scores) -> Scores:
        return 1.0 / (1.0 + np.exp(-(self.slope * scores + self.intercept)))

    def save(self, path: Path) -> None:
        model = {
            "features": list(NAMES),
            "slope": self.slope,
            "intercept": self.intercept,
            "booster": self.booster.model_to_string(),
        }
        path.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "Ranker":
        model = json.loads(path.read_text(encoding="utf-8"))
        if tuple(model["features"]) != NAMES:
            raise ValueError(f"{path} uses other features; run `python -m jend.ranker`")
        booster = lgb.Booster(model_str=model["booster"])
        return cls(booster, float(model["slope"]), float(model["intercept"]))


def folds(count: int, seed: int = 0) -> list[list[int]]:
    order = list(range(count))
    random.Random(seed).shuffle(order)
    return [sorted(order[start::FOLDS]) for start in range(FOLDS)]


def example(index: Index, query: Query, candidates: Candidates) -> Example:
    grades = query.grades()
    keys = (index.documents[row].key for row in positions(candidates.rows))
    return Example(candidates.features, np.array([grades.get(key, 0) for key in keys], np.int32))


def _booster(examples: Sequence[Example]) -> lgb.Booster:
    dataset = lgb.Dataset(
        np.vstack([example.features for example in examples]),
        label=np.concatenate([example.grades for example in examples]),
        group=[len(example.grades) for example in examples],
    )
    return lgb.train(PARAMETERS, dataset, num_boost_round=ROUNDS)  # pyright: ignore[reportUnknownMemberType]


def _logistic(scores: Scores, labels: Scores) -> tuple[float, float]:
    inputs = np.stack([scores, np.ones_like(scores)], axis=1)
    weights = np.zeros(2)
    for _ in range(50):
        predictions = 1.0 / (1.0 + np.exp(-(inputs @ weights)))
        gradient = inputs.T @ (predictions - labels) + 1e-3 * weights
        curvature = (inputs.T * (predictions * (1 - predictions))) @ inputs + 1e-3 * np.eye(2)
        weights -= np.linalg.solve(curvature, gradient)
    return float(weights[0]), float(weights[1])


def fit(examples: Sequence[Example]) -> Ranker:
    shown: list[Scores] = []
    answers: list[Scores] = []
    for fold in folds(len(examples)):
        held_out = set(fold)
        booster = _booster([e for position, e in enumerate(examples) if position not in held_out])
        for position in fold:
            features, grades = examples[position].features, examples[position].grades
            scores = np.asarray(booster.predict(features), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType]
            best = np.argsort(-scores, kind="stable")[:CALIBRATION_DEPTH]
            shown.append(scores[best])
            answers.append((grades[best] >= ANSWER).astype(np.float64))
    slope, intercept = _logistic(np.concatenate(shown), np.concatenate(answers))
    return Ranker(_booster(examples), slope, intercept)


async def query_vectors(data: Path, queries: Sequence[Query]) -> embed.Vectors:
    cache = embed.query_cache(data)
    try:
        async with openrouter.connect() as client:
            embedder = embed.QueryEmbedder(client, cache)
            return await embedder.embed([normalize(query.query) for query in queries])
    finally:
        cache.close()


def train(data: Path, benchmark: Path) -> Ranker:
    queries = load(benchmark)
    index = load_index(data)
    featurizer = Featurizer(index)
    vectors = asyncio.run(query_vectors(data, queries))
    examples = [
        example(index, query, featurizer.candidates(query.query, vector))
        for query, vector in zip(queries, vectors, strict=True)
    ]
    return fit(examples)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the ranker on the benchmark judgments.")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/benchmark.json"))
    args = parser.parse_args()
    ranker = train(args.data, args.benchmark)
    ranker.save(MODEL_PATH)
    print(f"saved {MODEL_PATH}: slope {ranker.slope:.3f}, intercept {ranker.intercept:.3f}")


if __name__ == "__main__":
    main()
