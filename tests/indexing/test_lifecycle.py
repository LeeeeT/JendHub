from jendhub.db.pool import Pool
from jendhub.hub.api import Release
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore
from jendhub.indexing.schedule import schedule_live
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.indexing.worker import Worker
from jendhub.search.engine import SearchEngine
from jendhub.search.highlights import Extent, read_extents, stored_lines, targets
from jendhub.search.ranking import load_model
from jendhub.vectors.index import VectorIndex
from tests.indexing.library_fixture import BASE, BOX, TAG
from tests.indexing.live import (
    APP,
    APP_SOURCE,
    KIT_SOURCE,
    KIT_V1,
    KIT_V2,
    WordEmbedder,
    index_live,
    store_package,
)


async def ids(pool: Pool, generation: int) -> set[str]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id FROM definitions WHERE generation = %s", (generation,)
        )
        return {id_ for (id_,) in await cursor.fetchall()}


async def highlighted(pool: Pool, generation: int) -> set[tuple[str, str]]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT release, module FROM highlights WHERE generation = %s", (generation,)
        )
        return set(await cursor.fetchall())


async def linked(pool: Pool, generation: int, release: str) -> dict[str, Extent]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT lines FROM highlights WHERE generation = %s AND release = %s",
            (generation, release),
        )
        [(lines,)] = await cursor.fetchall()
        return await read_extents(connection, generation, targets(stored_lines(lines)))


async def stored_users(pool: Pool, generation: int) -> dict[str, int]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT target_key, users FROM user_counts WHERE generation = %s AND users <> 0",
            (generation,),
        )
        return dict(await cursor.fetchall())


async def counted_users(pool: Pool, generation: int) -> dict[str, int]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT t.key, count(*) FROM uses JOIN definitions t ON t.point = uses.target_point"
            " WHERE t.generation = %(generation)s GROUP BY t.key"
            " UNION ALL SELECT target_key, count(*) FROM external_uses"
            " WHERE generation = %(generation)s GROUP BY target_key",
            {"generation": generation},
        )
        counted: dict[str, int] = {}
        for key, users in await cursor.fetchall():
            counted[key] = counted.get(key, 0) + users
        return counted


async def test_a_new_release_replaces_the_old_one(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await store_package(packages, KIT_V1, Release("kit", "1.0.0.0"), KIT_SOURCE)
    await store_package(packages, APP, Release("app", "1.0.0.0"), APP_SOURCE)
    index, target = await index_live(packages, vectors)
    assert await ids(pool, target.generation) == {
        "Nat",
        "Nat.add",
        "kit@1.0.0.0/main:double",
        "app@1.0.0.0/main:quadruple",
    }
    users = await stored_users(pool, target.generation)
    assert users == await counted_users(pool, target.generation)
    assert users["kit/main:double"] == 1
    assert "app/main:quadruple" not in users
    assert await highlighted(pool, target.generation) == {
        ("base", ""),
        (KIT_V1, "main"),
        (APP, "main"),
    }

    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), f"{BOX}\n{KIT_SOURCE[13:]}")
    await schedule_live(index, packages, vectors)
    worker = Worker(index, packages, vectors, WordEmbedder())
    await worker.drain(target.generation)
    found = await ids(pool, target.generation)
    assert "kit@1.0.0.0/main:double" not in found
    assert {"kit@2.0.0.0/main:double", "kit@2.0.0.0/main:open"} <= found
    assert await highlighted(pool, target.generation) == {
        ("base", ""),
        (KIT_V2, "main"),
        (APP, "main"),
    }
    assert (await linked(pool, target.generation, APP))["kit/main:double"] == Extent(
        "kit@2.0.0.0/main:double", 11, 14
    )
    users = await stored_users(pool, target.generation)
    assert users == await counted_users(pool, target.generation)
    assert users["kit/main:double"] == 1

    engine = SearchEngine(pool, vectors, WordEmbedder(), load_model(), target)
    [hits] = await engine.search(["double a number"], 3)
    assert hits[0].id == "kit@2.0.0.0/main:double"
    assert hits[0].package == "kit"
    assert hits[0].head.startswith("# Doubles a number.")


async def test_an_older_release_never_replaces_a_newer_one(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), KIT_SOURCE)
    _, target = await index_live(packages, vectors)
    index = IndexStore(pool)
    await index.enqueue(target.generation, [(KIT_V1, "kit")])
    await store_package(packages, KIT_V1, Release("kit", "1.0.0.0"), KIT_SOURCE)
    worker = Worker(index, packages, vectors, WordEmbedder())
    await worker.drain(target.generation)
    assert "kit@2.0.0.0/main:double" in await ids(pool, target.generation)
    assert "kit@1.0.0.0/main:double" not in await ids(pool, target.generation)


BROKEN = "import Base\n\ntype Nat is Data:\n  Zero{}\n"


async def test_an_invalid_release_falls_back_to_the_latest_valid_one(
    pool: Pool, packages: PackageStore, vectors: VectorIndex
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await store_package(packages, KIT_V1, Release("kit", "1.0.0.0"), KIT_SOURCE)
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN)
    index, target = await index_live(packages, vectors)
    found = await ids(pool, target.generation)
    assert "kit@1.0.0.0/main:double" in found
    assert not any(id_.startswith("kit@2.0.0.0") for id_ in found)
    assert await index.is_invalid(KIT_V2)
    assert await index.latest_valid_releases() == {"kit": KIT_V1}
    states = await index.job_states(target.generation)
    assert states == {"done": 2, "invalid": 1}


async def test_an_invalid_release_is_never_tried_again(
    packages: PackageStore, vectors: VectorIndex
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await store_package(packages, KIT_V2, Release("kit", "2.0.0.0"), BROKEN)
    index, _ = await index_live(packages, vectors)
    await packages.store_base(Base("v2.0.38", BASE.encode()))
    await schedule_live(index, packages, vectors)
    [_, new] = await index.generations(LIVE)
    assert await index.job_states(new.id) == {"queued": 1}


async def test_the_latest_valid_release_compares_versions_as_numbers(
    pool: Pool, packages: PackageStore
) -> None:
    await store_package(packages, KIT_V1, Release("kit", "0.9.0.0"), KIT_SOURCE)
    await store_package(packages, KIT_V2, Release("kit", "0.10.0.0"), KIT_SOURCE)
    assert await IndexStore(pool).latest_valid_releases() == {"kit": KIT_V2}
