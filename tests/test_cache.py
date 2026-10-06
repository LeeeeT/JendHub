from pathlib import Path

import numpy as np

from jend.cache import EmbeddingCache


def test_cache_returns_the_stored_vector_for_the_same_model_only(tmp_path: Path) -> None:
    vector = np.array([0.25, -0.5, 1.0], dtype=np.float32)
    cache = EmbeddingCache(tmp_path / "e.sqlite", "model-a")
    cache.put(["parse json"], [vector])
    cache.close()

    reopened = EmbeddingCache(tmp_path / "e.sqlite", "model-a")
    other = EmbeddingCache(tmp_path / "e.sqlite", "model-b")

    stored = reopened.get("parse json")
    assert stored is not None
    assert np.array_equal(stored, vector)
    assert reopened.get("parse toml") is None
    assert other.get("parse json") is None
