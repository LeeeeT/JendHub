import pytest

from jendhub.hub.api import HubError, Listing, Package, PackageRelease, Rejection, Release
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


async def test_a_release_cannot_change_its_content(packages: PackageStore) -> None:
    release = Release("kit", "0.1.0.0")
    await packages.store_package(Package(Listing(HASH_A, release), {"a.bend": b"a"}))
    await packages.store_package(Package(Listing(HASH_B, None), {"a.bend": b"b"}))
    with pytest.raises(HubError, match=r"the release kit@0\.1\.0\.0"):
        await packages.store_release(PackageRelease(HASH_B, release))
    assert await packages.holder(release) == HASH_A
    assert await packages.rejected_hashes() == set()


async def test_one_hash_has_several_names_and_versions(packages: PackageStore) -> None:
    await packages.store_package(Package(Listing(HASH_A, None), {"a.bend": b"a"}))
    releases = [Release("kit", "1.0.0.0"), Release("kit", "2.0.0.0"), Release("other", "1.0.0.0")]
    for release in releases:
        assert await packages.store_release(PackageRelease(HASH_A, release))
        assert await packages.holder(release) == HASH_A
        assert packages.library.release_hash(release) == HASH_A
    revision = await packages.revision()
    assert not await packages.store_release(PackageRelease(HASH_A, releases[0]))
    assert await packages.revision() == revision


async def test_latest_base_tag_is_the_last_stored(packages: PackageStore) -> None:
    assert await packages.latest_base_tag() is None
    await packages.store_base(Base("v2.0.37", b"old"))
    await packages.store_base(Base("v2.0.38", b"new"))
    assert await packages.latest_base_tag() == "v2.0.38"
    assert packages.library.base_source("v2.0.37").read_bytes() == b"old"


async def test_repairing_a_name_file_changes_the_library_revision(packages: PackageStore) -> None:
    release = PackageRelease(HASH_A, Release("kit", "1.0.0.0"))
    await packages.store_package(Package(Listing(HASH_A, release.release), {}))
    revision = await packages.revision()
    (packages.library.lib / "names" / str(release.release)).unlink()
    assert await packages.store_release(release)
    assert await packages.revision() > revision
    assert packages.library.release_hash(release.release) == HASH_A
