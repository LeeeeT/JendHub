from collections.abc import AsyncGenerator
from itertools import count

import numpy as np
import pytest

from jendhub.settings import QdrantSettings
from jendhub.vectors.index import (
    CODE,
    NAME,
    Projected,
    VectorConfig,
    VectorIndex,
    open_vector_index,
)

COLLECTIONS = count()
CONFIGS = [VectorConfig(8, "none"), VectorConfig(8, "int8"), VectorConfig(4, "binary")]


def basis(size: int, dimensions: int = 8) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
    vectors = np.zeros((size, dimensions), dtype=np.float32)
    for row in range(size):
        vectors[row, row % dimensions] = 1.0
        vectors[row, (row + 1) % dimensions] = 0.5
    return vectors


def projected(
    points: list[int], code: np.ndarray[tuple[int, int], np.dtype[np.float32]]
) -> Projected:
    return Projected(points, code, code[::-1].copy(), [f"module{point}" for point in points])


@pytest.fixture
async def index(qdrant_settings: QdrantSettings) -> AsyncGenerator[VectorIndex]:
    async with open_vector_index(qdrant_settings) as opened:
        yield opened


@pytest.mark.parametrize("config", CONFIGS, ids=lambda config: config.label)
async def test_search_finds_the_nearest_point(index: VectorIndex, config: VectorConfig) -> None:
    name = f"test-{next(COLLECTIONS)}"
    await index.create(name, config)
    vectors = basis(4)
    await index.upsert(name, config, projected([10, 11, 12, 13], vectors), "release")
    found = await index.search(name, config, vectors[2], 2)
    assert found[0].point == 12
    assert found[0].score == pytest.approx(1.0, abs=0.05)
    assert (found[0].release, found[0].module) == ("release", "module12")


async def test_delete_release_removes_only_its_points(index: VectorIndex) -> None:
    name = f"test-{next(COLLECTIONS)}"
    config = CONFIGS[0]
    await index.create(name, config)
    await index.upsert(name, config, projected([1, 2], basis(2)), "old")
    await index.upsert(name, config, projected([3], basis(3)[2:]), "new")
    await index.delete_release(name, "old")
    found = await index.search(name, config, basis(1)[0], 10)
    assert [neighbor.point for neighbor in found] == [3]


@pytest.mark.parametrize("config", CONFIGS, ids=lambda config: config.label)
async def test_scores_gives_the_exact_cosine_of_the_named_points(
    index: VectorIndex, config: VectorConfig
) -> None:
    name = f"test-{next(COLLECTIONS)}"
    await index.create(name, config)
    vectors = basis(3)
    await index.upsert(name, config, projected([5, 6, 7], vectors), "release")
    query = vectors[1]
    code = await index.scores(name, config, query, [6, 7, 8], CODE)
    names = await index.scores(name, config, query, [5], NAME)
    expected = config.project(vectors) @ config.project(query.reshape(1, -1))[0]
    assert sorted(code) == [6, 7]
    assert code[7] == pytest.approx(float(expected[2]), abs=1e-5)
    assert names[5] == pytest.approx(float(expected[2]), abs=1e-5)


async def test_create_twice_keeps_the_points(index: VectorIndex) -> None:
    name = f"test-{next(COLLECTIONS)}"
    config = CONFIGS[0]
    await index.create(name, config)
    await index.upsert(name, config, projected([1], basis(1)), "release")
    await index.create(name, config)
    assert [neighbor.point for neighbor in await index.search(name, config, basis(1)[0], 1)] == [1]
