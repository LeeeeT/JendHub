from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, PackageRelease, Release
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore
from jendhub.indexing.schedule import schedule_live
from jendhub.indexing.worker import Worker
from jendhub.search.filters import package_release
from jendhub.search.packages import read_package
from jendhub.search.sources import read_file
from jendhub.vectors.index import VectorIndex
from tests.indexing.library_fixture import BASE, TAG
from tests.indexing.live import APP, APP_SOURCE, KIT_SOURCE, KIT_V1, WordEmbedder, index_live


async def definition_ids(pool: Pool, generation: int) -> set[str]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id FROM definitions WHERE generation = %s",
            (generation,),
        )
        return {id_ for (id_,) in await cursor.fetchall()}


async def test_shared_content_indexes_each_name_and_its_latest_version(
    pool: Pool,
    packages: PackageStore,
    vectors: VectorIndex,
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await packages.store_package(Package(Listing(KIT_V1, None), {"main.bend": KIT_SOURCE.encode()}))
    for release in [
        Release("kit", "1.0.0.0"),
        Release("kit", "2.0.0.0"),
        Release("alias", "1.0.0.0"),
    ]:
        await packages.store_release(PackageRelease(KIT_V1, release))
    _, target = await index_live(packages, vectors)
    ids = await definition_ids(pool, target.generation)
    assert {"kit@2.0.0.0/main:double", "alias@1.0.0.0/main:double"} <= ids
    assert "kit@1.0.0.0/main:double" not in ids
    async with pool.connection() as connection:
        assert await package_release(connection, target.generation, "kit") == "kit@2.0.0.0"
        assert await package_release(connection, target.generation, "alias") == "alias@1.0.0.0"
    detail = await read_package(pool, target.generation, "kit", 0, 20)
    assert detail is not None
    assert [(r.version, r.state) for r in detail.releases] == [
        ("2.0.0.0", "indexed"),
        ("1.0.0.0", "unindexed"),
    ]
    for label in ["kit@1.0.0.0", "kit@2.0.0.0", "alias@1.0.0.0"]:
        source = await read_file(pool, packages.library, target.generation, label, "main.bend")
        assert source is not None
        assert source.text == KIT_SOURCE


async def test_a_new_name_binding_retries_a_release_that_lacked_its_dependency(
    pool: Pool,
    packages: PackageStore,
    vectors: VectorIndex,
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await packages.store_package(Package(Listing(KIT_V1, None), {"main.bend": KIT_SOURCE.encode()}))
    await packages.store_package(
        Package(
            Listing(APP, Release("app", "1.0.0.0")),
            {
                "main.bend": APP_SOURCE.encode(),
            },
        )
    )
    index, target = await index_live(packages, vectors)
    assert await index.is_invalid("app@1.0.0.0")
    await schedule_live(index, packages, vectors)
    assert await index.is_invalid("app@1.0.0.0")
    await packages.store_release(PackageRelease(KIT_V1, Release("unrelated", "1.0.0.0")))
    await schedule_live(index, packages, vectors)
    assert await index.is_invalid("app@1.0.0.0")
    await packages.store_release(PackageRelease(KIT_V1, Release("kit", "1.0.0.0")))
    await schedule_live(index, packages, vectors)
    assert not await index.is_invalid("app@1.0.0.0")
    await Worker(index, packages, vectors, WordEmbedder()).drain(target.generation)
    assert "app@1.0.0.0/main:quadruple" in await definition_ids(pool, target.generation)
    assert await index.job_states(target.generation) == {"done": 4}


async def test_each_file_links_to_the_name_that_it_imports(
    pool: Pool,
    packages: PackageStore,
    vectors: VectorIndex,
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await packages.store_package(
        Package(
            Listing(KIT_V1, Release("kit", "1.0.0.0")),
            {
                "main.bend": KIT_SOURCE.encode(),
            },
        )
    )
    await packages.store_release(PackageRelease(KIT_V1, Release("alias", "1.0.0.0")))
    await packages.store_package(
        Package(
            Listing(APP, Release("app", "1.0.0.0")),
            {
                "kit.bend": APP_SOURCE.encode(),
                "alias.bend": APP_SOURCE.replace("kit@", "alias@").encode(),
            },
        )
    )
    _, target = await index_live(packages, vectors)
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT d.module, e.target_key FROM definitions d"
            " JOIN external_uses e ON e.user_point = d.point"
            " WHERE d.generation = %s AND d.package = 'app' AND e.target_key <> 'Nat'",
            (target.generation,),
        )
        assert set(await cursor.fetchall()) == {
            ("kit", "kit/main:double"),
            ("alias", "alias/main:double"),
        }


async def test_new_unnamed_content_retries_a_missing_hash_dependency(
    pool: Pool,
    packages: PackageStore,
    vectors: VectorIndex,
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    source = APP_SOURCE.replace("kit@1.0.0.0", KIT_V1)
    await packages.store_package(
        Package(
            Listing(APP, Release("app", "1.0.0.0")),
            {
                "main.bend": source.encode(),
            },
        )
    )
    index, target = await index_live(packages, vectors)
    assert await index.is_invalid("app@1.0.0.0")
    await packages.store_package(Package(Listing(KIT_V1, None), {"main.bend": KIT_SOURCE.encode()}))
    await schedule_live(index, packages, vectors)
    assert not await index.is_invalid("app@1.0.0.0")
    await Worker(index, packages, vectors, WordEmbedder()).drain(target.generation)
    assert "app@1.0.0.0/main:quadruple" in await definition_ids(pool, target.generation)


async def test_unrelated_names_do_not_retry_a_structural_load_error(
    packages: PackageStore,
    vectors: VectorIndex,
) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    source = "import Base\n\ntype Nat is Data:\n  Zero{}\n"
    await packages.store_package(
        Package(
            Listing(KIT_V1, Release("kit", "1.0.0.0")),
            {
                "main.bend": source.encode(),
            },
        )
    )
    index, _ = await index_live(packages, vectors)
    assert await index.is_invalid("kit@1.0.0.0")
    await packages.store_release(PackageRelease(KIT_V1, Release("alias", "1.0.0.0")))
    await schedule_live(index, packages, vectors)
    assert await index.is_invalid("kit@1.0.0.0")
