from pathlib import Path

from jendhub.hub.api import Listing, Package, Release
from jendhub.hub.library import Library

HASH = "0x" + "a" * 32


def test_store_package_uses_the_bend_lib_layout(tmp_path: Path) -> None:
    library = Library(tmp_path)
    files = {"main.bend": b"main", "proofs/sorted.bend": b"sorted"}
    library.store_package(Package(Listing(HASH, Release("list-kit", "0.1.0.0")), files))
    assert (tmp_path / "lib" / HASH / "main.bend").read_bytes() == b"main"
    assert (tmp_path / "lib" / HASH / "proofs" / "sorted.bend").read_bytes() == b"sorted"
    assert (tmp_path / "lib" / "names" / "list-kit@0.1.0.0").read_text() == f"{HASH}\n"
    assert library.read_package(HASH) == files
    assert list(library.staging.iterdir()) == []


def test_store_package_keeps_an_unnamed_package_without_a_name_file(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.store_package(Package(Listing(HASH, None), {"main.bend": b"main"}))
    assert library.read_package(HASH) == {"main.bend": b"main"}
    assert not (tmp_path / "lib" / "names").exists()
