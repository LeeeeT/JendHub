from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, Rejection, Release
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore

HASH_A = "0x" + "a" * 32
HASH_B = "0x" + "b" * 32
HASH_C = "0x" + "c" * 32


async def test_known_hashes_hold_stored_and_rejected_packages(pool: Pool) -> None:
    store = PackageStore(pool)
    await store.store_package(Package(Listing(HASH_A, None), {}))
    await store.reject(Rejection(HASH_B, "invalid file name"))
    assert await store.known_hashes() == {HASH_A, HASH_B}


async def test_packages_after_return_the_files_in_store_order(pool: Pool) -> None:
    store = PackageStore(pool)
    await store.store_package(Package(Listing(HASH_A, None), {"a.bend": b"a"}))
    await store.store_package(Package(Listing(HASH_B, Release("kit", "0.1.0.0")), {}))
    found = await store.packages_after(0, 10)
    assert [item.package for item in found] == [
        Package(Listing(HASH_A, None), {"a.bend": b"a"}),
        Package(Listing(HASH_B, Release("kit", "0.1.0.0")), {}),
    ]
    assert await store.packages_after(found[0].sequence, 10) == found[1:]


async def test_latest_base_tag_is_the_last_stored(pool: Pool) -> None:
    store = PackageStore(pool)
    assert await store.latest_base_tag() is None
    await store.store_base(Base("v2.0.36", b"old"))
    await store.store_base(Base("v2.0.37", b"new"))
    assert await store.latest_base_tag() == "v2.0.37"
    assert await store.base("v2.0.36") == Base("v2.0.36", b"old")
