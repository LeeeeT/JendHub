import httpx
import respx

from jendhub.db.pool import Pool
from jendhub.hub.api import HUB, Listing, Rejection, Release
from jendhub.hub.base import LATEST_RELEASE, SOURCE, Base
from jendhub.hub.store import PackageStore
from jendhub.hub.sync import Plan, describe_plan, plan, sync
from tests.hub.fixture import listing, serve_package, serve_pages


def serve_base(router: respx.MockRouter, tag: str, source: bytes) -> respx.Route:
    router.get(LATEST_RELEASE).mock(httpx.Response(200, json={"tag_name": tag}))
    return router.get(SOURCE.format(tag=tag)).mock(httpx.Response(200, content=source))


async def test_sync_mirrors_new_packages_and_base(router: respx.MockRouter, pool: Pool) -> None:
    store = PackageStore(pool)
    serve_base(router, "v2.0.36", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(new, "kit", "0.2.0.0"), listing(old, "kit", "0.1.0.0")]])
    await sync(store)
    assert await store.known_hashes() == {old, new}
    assert (await store.package(new)).listing == Listing(new, Release("kit", "0.2.0.0"))
    assert await store.latest_base_tag() == "v2.0.36"


async def test_sync_rejects_a_package_with_an_invalid_file_name_once(
    router: respx.MockRouter, pool: Pool
) -> None:
    store = PackageStore(pool)
    serve_base(router, "v2.0.36", b"base")
    valid = serve_package(router, {"main.bend": b"main"})
    invalid = serve_package(router, {"main.bend": b"other", "notes~.txt": b"notes"})
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    await sync(store)
    assert await store.known_hashes() == {valid, invalid}
    assert await store.stored_hashes() == {valid}
    invalid_manifest = router.get(f"{HUB}/{invalid}/manifest")
    invalid_manifest.reset()
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    await sync(store)
    assert not invalid_manifest.called


async def test_sync_rejects_an_invalid_release_without_fetching_it(
    router: respx.MockRouter, pool: Pool
) -> None:
    store = PackageStore(pool)
    serve_base(router, "v2.0.36", b"base")
    invalid = serve_package(router, {"main.bend": b"main"})
    serve_pages(router, [[listing(invalid, "Bad_Name", "0.1.0.0")]])
    await sync(store)
    assert await store.known_hashes() == {invalid}
    assert await store.stored_hashes() == set()
    assert not router.get(f"{HUB}/{invalid}/manifest").called


async def test_sync_fetches_only_packages_that_the_store_does_not_know(
    router: respx.MockRouter, pool: Pool
) -> None:
    store = PackageStore(pool)
    serve_base(router, "v2.0.36", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(old)]])
    await sync(store)
    old_manifest = router.get(f"{HUB}/{old}/manifest")
    old_manifest.reset()
    serve_pages(router, [[listing(new), listing(old)]])
    await sync(store)
    assert await store.known_hashes() == {old, new}
    assert not old_manifest.called


async def test_sync_keeps_a_current_base(router: respx.MockRouter, pool: Pool) -> None:
    store = PackageStore(pool)
    await store.store_base(Base("v2.0.36", b"base"))
    source = serve_base(router, "v2.0.36", b"base")
    serve_pages(router, [[]])
    await sync(store)
    assert not source.called


def test_describe_plan_lists_each_step_oldest_first() -> None:
    hash_a = "0x" + "a" * 32
    hash_b = "0x" + "b" * 32
    hash_c = "0x" + "c" * 32
    entries: list[Listing | Rejection] = [
        Listing(hash_a, Release("kit", "0.1.0.0")),
        Listing(hash_b, None),
        Rejection(hash_c, "invalid release name or version"),
    ]
    assert describe_plan(Plan("v2.0.35", "v2.0.36", entries)) == "\n".join(
        [
            "fetch base.bend v2.0.36 (stored: v2.0.35)",
            f"fetch {hash_a} kit@0.1.0.0",
            f"fetch {hash_b}",
            f"reject {hash_c}: invalid release name or version",
            "new packages: 3, to fetch: 2, to reject: 1",
        ]
    )


def test_describe_plan_reports_a_current_base() -> None:
    expected = "base.bend is current at v2.0.36\nnew packages: 0, to fetch: 0, to reject: 0"
    assert describe_plan(Plan("v2.0.36", "v2.0.36", [])) == expected


async def test_plan_changes_nothing(router: respx.MockRouter, pool: Pool) -> None:
    store = PackageStore(pool)
    source = serve_base(router, "v2.0.36", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(new), listing(old, "Bad_Name", "0.1.0.0")]])
    planned = await plan(store)
    assert [entry.hash for entry in planned.entries] == [old, new]
    assert await store.known_hashes() == set()
    assert not source.called
    assert not router.get(f"{HUB}/{new}/manifest").called
