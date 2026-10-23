import asyncio
from datetime import timedelta

import psycopg
import pytest

from jendhub.db.pool import Pool
from jendhub.embedding.model import Vectors
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore
from jendhub.indexing import schedule, worker
from jendhub.indexing.schedule import clean_retired, schedule_live, wait_until_live
from jendhub.indexing.store import LIVE, MAX_ATTEMPTS, IndexStore, Job
from jendhub.indexing.worker import Attempt, IndexingError, Worker
from jendhub.search.engine import SearchEngine, Target
from jendhub.search.ranking import load_model
from jendhub.vectors.index import INDEXING_THRESHOLD, VectorConfig, VectorIndex
from tests.indexing.library_fixture import BASE
from tests.indexing.live import POLL_SECONDS, WordEmbedder, index_live, seed

NEW_TAG = "v2.0.39"


class FailingEmbedder:
    async def embed(self, texts: list[str]) -> Vectors:
        raise RuntimeError(f"no vectors for {len(texts)} texts")


async def states(index: IndexStore, corpus: str) -> list[str]:
    return [generation.state for generation in await index.generations(corpus)]


async def test_a_new_base_builds_a_new_generation_beside_the_live_one(
    packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(packages)
    index, old = await index_live(packages, vectors)
    await packages.store_base(Base(NEW_TAG, BASE.encode()))
    await schedule_live(index, packages, vectors)
    assert await states(index, LIVE) == ["live", "building"]
    [_, new] = await index.generations(LIVE)
    assert await vectors.indexing_threshold(new.collection) == 0
    assert await vectors.indexing_threshold(old.collection) == INDEXING_THRESHOLD
    worker = Worker(index, packages, vectors, WordEmbedder())
    await worker.drain(new.id)
    await wait_until_live(index, vectors, new, POLL_SECONDS)
    assert await states(index, LIVE) == ["live"]
    assert await vectors.indexing_threshold(new.collection) == INDEXING_THRESHOLD
    assert (await index.generations(LIVE))[0].base_tag == NEW_TAG
    monkeypatch.setattr(schedule, "RETIREMENT_GRACE", timedelta(0))
    await clean_retired(index, vectors)
    assert not await vectors.client.collection_exists(old.collection)
    with pytest.raises(KeyError):
        await index.generation(old.generation)


async def test_a_new_vector_setting_builds_a_new_generation_beside_the_live_one(
    pool: Pool, packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(packages)
    index, old = await index_live(packages, vectors)
    setting = VectorConfig(1024, "int8")
    monkeypatch.setattr(schedule, "PRODUCTION", setting)
    await schedule_live(index, packages, vectors)
    assert await states(index, LIVE) == ["live", "building"]
    live = await index.live(LIVE)
    assert live is not None
    assert (live.id, live.config) == (old.generation, old.config)
    [_, new] = await index.generations(LIVE)
    assert new.config == setting
    assert await vectors.exists(new.collection)
    worker = Worker(index, packages, vectors, WordEmbedder())
    await worker.drain(new.id)
    await wait_until_live(index, vectors, new, POLL_SECONDS)
    live = await index.live(LIVE)
    assert live is not None
    assert (live.id, live.config) == (new.id, setting)
    engine = SearchEngine(
        pool, vectors, WordEmbedder(), load_model(), Target(live.id, live.collection, setting)
    )
    [hits] = await engine.search(["double a number"], 10)
    assert "kit@1.0.0.0/main:double" in [hit.id for hit in hits]


async def test_a_failed_job_waits_and_then_stops_the_drain(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await seed(packages)
    index = IndexStore(pool)
    await schedule_live(index, packages, vectors)
    [generation] = await index.generations(LIVE)
    worker = Worker(index, packages, vectors, FailingEmbedder())
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


async def test_a_job_whose_worker_stops_each_time_fails_at_last(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await seed(packages)
    index = IndexStore(pool)
    await schedule_live(index, packages, vectors)
    [generation] = await index.generations(LIVE)
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE index_jobs SET state = 'running', attempts = %s,"
            " lease_until = now() - interval '1 minute'",
            (MAX_ATTEMPTS,),
        )
    worker = Worker(index, packages, vectors, FailingEmbedder())
    assert not await worker.step()
    assert await index.job_states(generation.id) == {"failed": 3}


async def test_a_worker_serves_on_after_a_step_fails(
    packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "IDLE_SECONDS", POLL_SECONDS)
    await seed(packages)
    index = IndexStore(packages.pool)
    await schedule_live(index, packages, vectors)
    [generation] = await index.generations(LIVE)
    serving = Worker(index, packages, vectors, WordEmbedder())
    failed: list[str] = []

    async def flaky(job: Job) -> Attempt:
        if not failed:
            failed.append(job.release)
            raise psycopg.OperationalError("the server closed the connection")
        return await serving.attempt(job)

    stop = asyncio.Event()
    served = asyncio.create_task(serving.serve(stop, flaky))
    async with asyncio.timeout(60):
        while (await index.job_states(generation.id)).get("done", 0) < 2:
            assert not served.done()
            await asyncio.sleep(POLL_SECONDS)
    stop.set()
    await served


async def test_a_base_that_does_not_load_fails_with_its_load_error(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await packages.store_base(Base(NEW_TAG, b"type Nat is Data:\n  Zero{\n"))
    index = IndexStore(pool)
    await schedule_live(index, packages, vectors)
    assert await Worker(index, packages, vectors, WordEmbedder()).step()
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT state, error FROM index_jobs")
        [(state, error)] = await cursor.fetchall()
        cursor = await connection.execute("SELECT count(*) FROM invalid_releases")
        assert await cursor.fetchone() == (0,)
    assert state == "queued"
    assert error.startswith("Base does not load: expected")
