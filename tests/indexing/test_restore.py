from pathlib import Path

from jendhub.db.pool import Pool
from jendhub.embedding.cache import EmbeddingCache
from jendhub.indexing.project import restore_vectors
from jendhub.search.engine import SearchEngine
from jendhub.vectors.index import VectorIndex
from tests.indexing.live import WordEmbedder, index_live, seed


async def test_restore_writes_a_lost_collection_from_the_cache(
    pool: Pool, vectors: VectorIndex, tmp_path: Path
) -> None:
    await seed(pool)
    index, target = await index_live(pool, vectors, tmp_path)
    engine = SearchEngine(pool, vectors, WordEmbedder(), target)
    [before] = await engine.search(["double a number"], 3)
    await vectors.drop(target.collection)
    assert await restore_vectors(index, vectors, EmbeddingCache(pool)) == [target.collection]
    [after] = await engine.search(["double a number"], 3)
    assert [hit.id for hit in after] == [hit.id for hit in before]


async def test_restore_leaves_a_present_collection_alone(
    pool: Pool, vectors: VectorIndex, tmp_path: Path
) -> None:
    await seed(pool)
    index, _ = await index_live(pool, vectors, tmp_path)
    assert await restore_vectors(index, vectors, EmbeddingCache(pool)) == []
