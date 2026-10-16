from collections.abc import AsyncGenerator
from itertools import count

import numpy as np
import pytest

from jendhub.settings import QdrantSettings
from jendhub.vectors.index import VectorConfig, VectorIndex, open_vector_index

COLLECTIONS = count()
CONFIGS = [VectorConfig(8, "none"), VectorConfig(8, "int8"), VectorConfig(4, "binary")]


def basis(size: int, dimensions: int = 8) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
    vectors = np.zeros((size, dimensions), dtype=np.float32)
    for row in range(size):
        vectors[row, row % dimensions] = 1.0
        vectors[row, (row + 1) % dimensions] = 0.5
    return vectors


@pytest.fixture
async def index(qdrant_settings: QdrantSettings) -> AsyncGenerator[VectorIndex]:
    async with open_vector_index(qdrant_settings) as opened:
        yield opened


@pytest.mark.parametrize("config", CONFIGS, ids=lambda config: config.label)
async def test_search_finds_the_nearest_point(index: VectorIndex, config: VectorConfig) -> None:
    name = f"test-{next(COLLECTIONS)}"
    await index.create(name, config)
    vectors = basis(4)
    await index.upsert(name, config, [10, 11, 12, 13], vectors, "release")
    found = await index.search(name, config, vectors[2], 2)
    assert found.points[0] == 12
    assert found.scores[0] == pytest.approx(1.0, abs=0.05)


async def test_delete_release_removes_only_its_points(index: VectorIndex) -> None:
    name = f"test-{next(COLLECTIONS)}"
    config = CONFIGS[0]
    await index.create(name, config)
    await index.upsert(name, config, [1, 2], basis(2), "old")
    await index.upsert(name, config, [3], basis(3)[2:], "new")
    await index.delete_release(name, "old")
    found = await index.search(name, config, basis(1)[0], 10)
    assert found.points == [3]


async def test_vectors_returns_the_projected_vectors(index: VectorIndex) -> None:
    name = f"test-{next(COLLECTIONS)}"
    config = VectorConfig(4, "none")
    await index.create(name, config)
    vectors = basis(2)
    await index.upsert(name, config, [5, 6], vectors, "release")
    found = await index.vectors(name, [6, 7])
    assert list(found) == [6]
    assert np.allclose(found[6], config.project(vectors)[1])


async def test_create_twice_keeps_the_points(index: VectorIndex) -> None:
    name = f"test-{next(COLLECTIONS)}"
    config = CONFIGS[0]
    await index.create(name, config)
    await index.upsert(name, config, [1], basis(1), "release")
    await index.create(name, config)
    assert (await index.search(name, config, basis(1)[0], 1)).points == [1]
