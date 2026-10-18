from pathlib import Path

import pytest

from jendhub.db.pool import Pool
from jendhub.embedding.cache import EmbeddingCache
from jendhub.indexing import project, worker
from jendhub.indexing.project import restore_vectors
from jendhub.search.engine import SearchEngine
from jendhub.search.ranking import load_model
from jendhub.vectors.index import VectorIndex
from tests.indexing.live import KIT_V1, WordEmbedder, index_live, seed


async def test_restore_leaves_a_present_collection_alone(
    pool: Pool, vectors: VectorIndex, tmp_path: Path
) -> None:
    await seed(pool)
    index, _ = await index_live(pool, vectors, tmp_path)
    assert await restore_vectors(index, vectors, EmbeddingCache(pool)) == []


async def test_restore_completes_a_partial_collection(
    pool: Pool, vectors: VectorIndex, tmp_path: Path
) -> None:
    await seed(pool)
    index, target = await index_live(pool, vectors, tmp_path)
    engine = SearchEngine(pool, vectors, WordEmbedder(), load_model(), target)
    [before] = await engine.search(["double a number"], 3)
    described = await vectors.client.get_collection(target.collection)
    threshold = described.config.optimizer_config.indexing_threshold
    await vectors.delete_release(target.collection, KIT_V1)
    assert await restore_vectors(index, vectors, EmbeddingCache(pool)) == [target.collection]
    [after] = await engine.search(["double a number"], 3)
    assert [hit.id for hit in after] == [hit.id for hit in before]
    restored = await vectors.client.get_collection(target.collection)
    assert restored.config.optimizer_config.indexing_threshold == threshold


async def test_indexing_and_restore_work_in_chunks(
    pool: Pool, vectors: VectorIndex, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "CHUNK", 1)
    monkeypatch.setattr(project, "CHUNK", 1)
    await seed(pool)
    index, target = await index_live(pool, vectors, tmp_path)
    engine = SearchEngine(pool, vectors, WordEmbedder(), load_model(), target)
    [before] = await engine.search(["double a number"], 3)
    assert before[0].id == "kit@1.0.0.0/main:double"
    await vectors.drop(target.collection)
    assert await restore_vectors(index, vectors, EmbeddingCache(pool)) == [target.collection]
    [after] = await engine.search(["double a number"], 3)
    assert [hit.id for hit in after] == [hit.id for hit in before]
