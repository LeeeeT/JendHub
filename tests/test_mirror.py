from pathlib import Path

from jendhub.base import Base
from jendhub.hub import Listing, Package, Rejection, Release
from jendhub.mirror import Mirror

HASH = "0x" + "a" * 32


def test_store_package_uses_the_bend_lib_layout(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    files = {"main.bend": b"main", "proofs/sorted.bend": b"sorted"}
    mirror.store_package(Package(Listing(HASH, Release("list-kit", "0.1.0.0")), files))
    assert (tmp_path / "lib" / HASH / "main.bend").read_bytes() == b"main"
    assert (tmp_path / "lib" / HASH / "proofs" / "sorted.bend").read_bytes() == b"sorted"
    assert (tmp_path / "lib" / "names" / "list-kit@0.1.0.0").read_text() == f"{HASH}\n"
    assert mirror.hashes() == {HASH}
    assert list(mirror.staging.iterdir()) == []


def test_store_package_keeps_an_unnamed_package_without_a_name_file(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    mirror.store_package(Package(Listing(HASH, None), {"main.bend": b"main"}))
    assert mirror.hashes() == {HASH}
    assert not (tmp_path / "lib" / "names").exists()


def test_store_package_keeps_a_package_without_bend_files(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    mirror.store_package(Package(Listing(HASH, None), {}))
    assert mirror.hashes() == {HASH}


def test_hashes_ignores_the_names_directory(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    mirror.store_package(Package(Listing(HASH, Release("kit", "0.1.0.0")), {}))
    assert mirror.hashes() == {HASH}


def test_reject_records_the_hash_and_the_reason(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    mirror.reject(Rejection(HASH, "invalid file name 'a b.bend'"))
    assert mirror.rejected_hashes() == {HASH}
    assert mirror.hashes() == set()
    assert (tmp_path / "rejected" / HASH).read_text() == "invalid file name 'a b.bend'\n"


def test_store_base_records_its_tag(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path)
    assert mirror.stored_base_tag() is None
    mirror.store_base(Base("v2.0.36", b"type Unit is Data:\n"))
    assert mirror.stored_base_tag() == "v2.0.36"
    assert mirror.base_source.read_bytes() == b"type Unit is Data:\n"
