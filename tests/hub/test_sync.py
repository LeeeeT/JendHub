import httpx
import respx

from jendhub.hub.api import HUB, Listing, Release
from jendhub.hub.base import LATEST_RELEASE, SOURCE, Base
from jendhub.hub.store import PackageStore
from jendhub.hub.sync import plan, sync
from tests.hub.fixture import listing, serve_package, serve_pages


def serve_base(router: respx.MockRouter, tag: str, source: bytes) -> respx.Route:
    router.get(LATEST_RELEASE).mock(httpx.Response(200, json={"tag_name": tag}))
    return router.get(SOURCE.format(tag=tag)).mock(httpx.Response(200, content=source))


async def test_sync_mirrors_new_packages_and_base(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_base(router, "v2.0.38", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(new, "kit", "0.2.0.0"), listing(old, "kit", "0.1.0.0")]])
    await sync(packages)
    assert await packages.known_hashes() == {old, new}
    stored = await packages.package(new)
    assert stored.listing == Listing(new, Release("kit", "0.2.0.0"))
    assert stored.files == {"new.bend": b"new"}
    assert await packages.latest_base_tag() == "v2.0.38"
    assert packages.library.base_source("v2.0.38").read_bytes() == b"base"


async def test_sync_rejects_a_package_with_an_invalid_file_name_once(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_base(router, "v2.0.38", b"base")
    valid = serve_package(router, {"main.bend": b"main"})
    invalid = serve_package(router, {"main.bend": b"other", "notes~.txt": b"notes"})
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    await sync(packages)
    assert await packages.known_hashes() == {valid, invalid}
    assert await packages.stored_hashes() == {valid}
    invalid_manifest = router.get(f"{HUB}/{invalid}/manifest")
    invalid_manifest.reset()
    serve_pages(router, [[listing(invalid, "kit", "0.2.0.0"), listing(valid, "kit", "0.1.0.0")]])
    await sync(packages)
    assert not invalid_manifest.called


async def test_sync_rejects_an_invalid_release_without_fetching_it(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_base(router, "v2.0.38", b"base")
    invalid = serve_package(router, {"main.bend": b"main"})
    serve_pages(router, [[listing(invalid, "Bad_Name", "0.1.0.0")]])
    await sync(packages)
    assert await packages.known_hashes() == {invalid}
    assert await packages.stored_hashes() == set()
    assert not router.get(f"{HUB}/{invalid}/manifest").called


async def test_sync_fetches_only_packages_that_the_store_does_not_know(
    router: respx.MockRouter, packages: PackageStore
) -> None:
    serve_base(router, "v2.0.38", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(old)]])
    await sync(packages)
    old_manifest = router.get(f"{HUB}/{old}/manifest")
    old_manifest.reset()
    serve_pages(router, [[listing(new), listing(old)]])
    await sync(packages)
    assert await packages.known_hashes() == {old, new}
    assert not old_manifest.called


async def test_sync_keeps_a_current_base(router: respx.MockRouter, packages: PackageStore) -> None:
    await packages.store_base(Base("v2.0.38", b"base"))
    source = serve_base(router, "v2.0.38", b"base")
    serve_pages(router, [[]])
    await sync(packages)
    assert not source.called


async def test_plan_changes_nothing(router: respx.MockRouter, packages: PackageStore) -> None:
    source = serve_base(router, "v2.0.38", b"base")
    old = serve_package(router, {"old.bend": b"old"})
    new = serve_package(router, {"new.bend": b"new"})
    serve_pages(router, [[listing(new), listing(old, "Bad_Name", "0.1.0.0")]])
    planned = await plan(packages)
    assert [entry.hash for entry in planned.entries] == [old, new]
    assert await packages.known_hashes() == set()
    assert not source.called
    assert not router.get(f"{HUB}/{new}/manifest").called
