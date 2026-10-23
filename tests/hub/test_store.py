from jendhub.hub.api import Listing, Package, Rejection, Release
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore

HASH_A = "0x" + "a" * 32
HASH_B = "0x" + "b" * 32


async def test_known_hashes_hold_stored_and_rejected_packages(packages: PackageStore) -> None:
    await packages.store_package(Package(Listing(HASH_A, None), {}))
    await packages.reject(Rejection(HASH_B, "invalid file name"))
    assert await packages.known_hashes() == {HASH_A, HASH_B}


async def test_a_stored_package_keeps_its_files_in_the_library(packages: PackageStore) -> None:
    package = Package(Listing(HASH_A, Release("kit", "0.1.0.0")), {"src/a.bend": b"a"})
    await packages.store_package(package)
    assert await packages.package(HASH_A) == package
    assert packages.library.read_package(HASH_A) == {"src/a.bend": b"a"}


async def test_a_package_with_the_release_of_another_is_rejected(
    packages: PackageStore,
) -> None:
    release = Release("kit", "0.1.0.0")
    await packages.store_package(Package(Listing(HASH_A, release), {"a.bend": b"a"}))
    await packages.store_package(Package(Listing(HASH_A, release), {"a.bend": b"a"}))
    taken = await packages.store_package(Package(Listing(HASH_B, release), {"a.bend": b"b"}))
    assert taken == Rejection(HASH_B, f"the release kit@0.1.0.0 is {HASH_A}")
    assert await packages.rejected_hashes() == {HASH_B}
    assert not packages.library.package_directory(HASH_B).exists()


async def test_latest_base_tag_is_the_last_stored(packages: PackageStore) -> None:
    assert await packages.latest_base_tag() is None
    await packages.store_base(Base("v2.0.37", b"old"))
    await packages.store_base(Base("v2.0.38", b"new"))
    assert await packages.latest_base_tag() == "v2.0.38"
    assert packages.library.base_source("v2.0.37").read_bytes() == b"old"
