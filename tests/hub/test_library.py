from pathlib import Path

from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library, ensure_base, refresh
from jendhub.hub.store import PackageStore

HASH = "0x" + "a" * 32
OTHER = "0x" + "b" * 32


def test_store_package_uses_the_bend_lib_layout(tmp_path: Path) -> None:
    library = Library(tmp_path)
    files = {"main.bend": b"main", "proofs/sorted.bend": b"sorted"}
    library.store_package(Package(Listing(HASH, Release("list-kit", "0.1.0.0")), files))
    assert (tmp_path / "lib" / HASH / "main.bend").read_bytes() == b"main"
    assert (tmp_path / "lib" / HASH / "proofs" / "sorted.bend").read_bytes() == b"sorted"
    assert library.releases() == {"list-kit@0.1.0.0": HASH}
    assert library.read_package(HASH) == files
    assert list(library.staging.iterdir()) == []


def test_store_package_keeps_an_unnamed_package_without_a_name_file(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.store_package(Package(Listing(HASH, None), {"main.bend": b"main"}))
    assert library.read_package(HASH) == {"main.bend": b"main"}
    assert library.releases() == {}


async def test_refresh_copies_each_new_package_once(tmp_path: Path, pool: Pool) -> None:
    store = PackageStore(pool)
    library = Library(tmp_path)
    await store.store_package(Package(Listing(HASH, Release("kit", "0.1.0.0")), {"a.bend": b"a"}))
    assert await refresh(library, store) == 1
    await store.store_package(Package(Listing(OTHER, None), {"b.bend": b"b"}))
    assert await refresh(library, store) == 1
    assert await refresh(library, store) == 0
    assert library.read_package(HASH) == {"a.bend": b"a"}
    assert library.read_package(OTHER) == {"b.bend": b"b"}


async def test_ensure_base_writes_the_source_of_a_tag(tmp_path: Path, pool: Pool) -> None:
    store = PackageStore(pool)
    await store.store_base(Base("v2.0.36", b"type Unit is Data:\n"))
    path = await ensure_base(Library(tmp_path), store, "v2.0.36")
    assert path.read_bytes() == b"type Unit is Data:\n"
