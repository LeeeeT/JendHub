from datetime import timedelta
from pathlib import Path

import pytest

from jendhub.db.pool import Pool
from jendhub.embedding.model import Vectors
from jendhub.hub.base import Base
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from jendhub.indexing import schedule
from jendhub.indexing.schedule import clean_retired, promote_ready, schedule_live
from jendhub.indexing.store import LIVE, MAX_ATTEMPTS, IndexStore
from jendhub.indexing.worker import IndexingError, Worker
from jendhub.vectors.index import VectorIndex
from tests.indexing.library_fixture import BASE
from tests.indexing.live import WordEmbedder, index_live, seed

NEW_TAG = "v2.0.38"


class FailingEmbedder:
    async def embed(self, texts: list[str]) -> Vectors:
        raise RuntimeError(f"no vectors for {len(texts)} texts")


async def states(index: IndexStore, corpus: str) -> list[str]:
    return [generation.state for generation in await index.generations(corpus)]


async def test_a_new_base_builds_a_new_generation_beside_the_live_one(
    pool: Pool, vectors: VectorIndex, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packages = await seed(pool)
    index, old = await index_live(pool, vectors, tmp_path)
    await packages.store_base(Base(NEW_TAG, BASE.encode()))
    await schedule_live(index, packages, vectors)
    assert await states(index, LIVE) == ["live", "building"]
    [_, new] = await index.generations(LIVE)
    worker = Worker(index, packages, Library(tmp_path), vectors, WordEmbedder())
    await worker.drain(new.id)
    await promote_ready(index, LIVE)
    assert await states(index, LIVE) == ["live"]
    assert (await index.generations(LIVE))[0].base_tag == NEW_TAG
    monkeypatch.setattr(schedule, "RETIREMENT_GRACE", timedelta(0))
    await clean_retired(index, vectors)
    assert not await vectors.client.collection_exists(old.collection)
    with pytest.raises(KeyError):
        await index.generation(old.generation)


async def test_a_failed_job_waits_and_then_stops_the_drain(
    pool: Pool, vectors: VectorIndex, tmp_path: Path
) -> None:
    packages: PackageStore = await seed(pool)
    index = IndexStore(pool)
    await schedule_live(index, packages, vectors)
    [generation] = await index.generations(LIVE)
    worker = Worker(index, packages, Library(tmp_path), vectors, FailingEmbedder())
    while await worker.step():
        pass
    assert await index.job_states(generation.id) == {"queued": 3}
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE index_jobs SET attempts = %s, run_after = now()", (MAX_ATTEMPTS - 1,)
        )
    while await worker.step():
        pass
    assert await index.job_states(generation.id) == {"failed": 3}
    with pytest.raises(IndexingError, match="failed"):
        await worker.drain(generation.id)
    await index.retry_failed(generation.id)
    assert (await index.job_states(generation.id)).get("queued")
