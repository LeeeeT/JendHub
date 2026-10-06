from pathlib import Path

import numpy as np

from jend.features import NAMES
from jend.ranker import Example, Ranker, fit, folds


def _examples(count: int) -> list[Example]:
    generator = np.random.default_rng(1)
    examples: list[Example] = []
    for _ in range(count):
        features = generator.normal(size=(60, len(NAMES))).astype(np.float32)
        grades = np.digitize(features[:, 0], [0.5, 1.2, 1.8]).astype(np.int32)
        examples.append(Example(features, grades))
    return examples


def test_folds_split_all_queries_into_disjoint_parts() -> None:
    parts = folds(23)

    assert sorted(position for part in parts for position in part) == list(range(23))
    assert folds(23) == parts


def test_fit_ranks_by_the_informative_feature_and_survives_a_round_trip(tmp_path: Path) -> None:
    ranker = fit(_examples(30))
    held_out = _examples(31)[-1]

    scores = ranker.scores(held_out.features)
    best = np.argsort(-scores)[:5]
    assert held_out.grades[best].mean() > held_out.grades.mean()
    assert np.all(np.diff(ranker.probabilities(np.sort(scores))) >= 0)

    ranker.save(tmp_path / "ranker.json")
    loaded = Ranker.load(tmp_path / "ranker.json")
    assert np.allclose(loaded.scores(held_out.features), scores)


def test_shipped_model_matches_the_feature_names() -> None:
    ranker = Ranker.load()

    assert ranker.booster.num_feature() == len(NAMES)
    assert ranker.slope > 0
