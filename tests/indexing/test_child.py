from datetime import timedelta

import pytest

from jendhub.db.pool import Pool
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore
from jendhub.indexing import worker as worker_module
from jendhub.indexing.schedule import schedule_live
from jendhub.indexing.store import LIVE
from jendhub.indexing.worker import Worker
from jendhub.settings import DatabaseSettings, QdrantSettings
from jendhub.vectors.index import VectorIndex
from tests.indexing.library_fixture import BASE
from tests.indexing.live import WordEmbedder, index_live, seed


@pytest.fixture
def child_environment(
    monkeypatch: pytest.MonkeyPatch,
    database: DatabaseSettings,
    qdrant_settings: QdrantSettings,
    packages: PackageStore,
) -> None:
    monkeypatch.setenv("JENDHUB_DATABASE_URL", database.database_url)
    monkeypatch.setenv("JENDHUB_QDRANT_URL", qdrant_settings.qdrant_url)
    monkeypatch.setenv("JENDHUB_LIBRARY_PATH", str(packages.library.root))
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused")


async def new_generation(packages: PackageStore, vectors: VectorIndex) -> tuple[Worker, int]:
    await seed(packages)
    index, _ = await index_live(packages, vectors)
    await packages.store_base(Base("v2.0.38", BASE.encode()))
    await schedule_live(index, packages, vectors)
    [_, new] = await index.generations(LIVE)
    return Worker(index, packages, vectors, WordEmbedder()), new.id


@pytest.mark.usefixtures("child_environment")
async def test_each_job_runs_in_its_own_process(
    packages: PackageStore, vectors: VectorIndex
) -> None:
    worker, generation = await new_generation(packages, vectors)
    while await worker.step(worker.attempt_in_child):
        pass
    assert await worker.index.job_states(generation) == {"done": 3}


@pytest.mark.usefixtures("child_environment")
async def test_a_job_whose_process_stops_is_tried_again(
    pool: Pool, packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, generation = await new_generation(packages, vectors)
    monkeypatch.setenv("JENDHUB_DATABASE_URL", "postgresql:///nothing?host=/nothing")
    assert await worker.step(worker.attempt_in_child)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT state, error FROM index_jobs WHERE generation = %s AND attempts = 1",
            (generation,),
        )
        assert await cursor.fetchall() == [
            ("queued", "its indexing process stopped with exit code 1")
        ]


async def test_a_job_whose_process_runs_past_its_lease_is_tried_again(
    pool: Pool, packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, generation = await new_generation(packages, vectors)
    monkeypatch.setattr(worker_module, "CHILD", "tests.indexing.hang")
    monkeypatch.setattr(worker_module, "LEASE", timedelta(seconds=0.5))
    assert await worker.step(worker.attempt_in_child)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT state, error FROM index_jobs WHERE generation = %s AND attempts = 1",
            (generation,),
        )
        assert await cursor.fetchall() == [
            ("queued", "its indexing process ran for more than 0.5 s")
        ]


async def test_a_job_whose_process_gives_no_outcome_is_tried_again(
    pool: Pool, packages: PackageStore, vectors: VectorIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, generation = await new_generation(packages, vectors)
    monkeypatch.setattr(worker_module, "CHILD", "tests.indexing.silent")
    assert await worker.step(worker.attempt_in_child)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT state, error FROM index_jobs WHERE generation = %s AND attempts = 1",
            (generation,),
        )
        assert await cursor.fetchall() == [("queued", "its indexing process gave no outcome")]
