from pathlib import Path

import pytest

from jendhub.hub.api import Release
from jendhub.hub.library import Library
from jendhub.indexing.artifacts import Artifacts, IndexedDefinition, build
from jendhub.indexing.failures import Place
from jendhub.search.sources import BASE_RELEASE
from jendhub.uses import resolve
from jendhub.uses.resolve import UsesError
from tests.indexing.library_fixture import BOX, LIB, LIB_HASH, OTHER, OTHER_HASH, TAG, library

OWNERS = {LIB_HASH: LIB, OTHER_HASH: OTHER}


async def owners(hashes: list[str]) -> dict[str, Release | None]:
    return {hash_: OWNERS.get(hash_) for hash_ in hashes}


async def artifacts(found: Library, hash_: str | None) -> Artifacts:
    base = found.base_source(TAG)
    if hash_ is None:
        return await build(found, base, BASE_RELEASE, None, owners)
    return await build(found, base, hash_, OWNERS[hash_], owners)


def by_id(found: Artifacts) -> dict[str, IndexedDefinition]:
    return {indexed.definition.id: indexed for indexed in found.definitions}


async def test_uses_cross_the_modules_of_a_package(tmp_path: Path) -> None:
    main = "import Base\nimport ./box.bend as B\n\ndef root(x: B.Box) -> Nat:\n  B.open(x)\n"
    found = await artifacts(
        library(tmp_path, {LIB: (LIB_HASH, {"box.bend": BOX, "main.bend": main})}), LIB_HASH
    )
    assert found.uses == [
        ("lib@1.0.0.0/box:open", "lib@1.0.0.0/box:Box"),
        ("lib@1.0.0.0/main:root", "lib@1.0.0.0/box:Box"),
        ("lib@1.0.0.0/main:root", "lib@1.0.0.0/box:open"),
    ]
    indexed = by_id(found)
    assert indexed["lib@1.0.0.0/box:Box"].release_users == 2
    assert indexed["lib@1.0.0.0/main:root"].release_users == 0
    assert indexed["lib@1.0.0.0/box:open"].external_targets == {"Nat"}
    assert found.errors == {}
    assert found.mismatches == {}


async def test_uses_of_base_and_other_packages_are_external_keys(tmp_path: Path) -> None:
    main = (
        "import Base\nimport other@1.0.0.0/box.bend as B\n\n"
        "def root(x: Nat) -> Nat:\n  (B.open(B.Box{x}) + Zero{} : Nat)\n"
    )
    found = await artifacts(
        library(
            tmp_path,
            {LIB: (LIB_HASH, {"main.bend": main}), OTHER: (OTHER_HASH, {"box.bend": BOX})},
        ),
        LIB_HASH,
    )
    assert found.uses == []
    assert by_id(found)["lib@1.0.0.0/main:root"].external_targets == {
        "Nat",
        "Nat.add",
        "other/box:Box",
        "other/box:open",
    }


async def test_base_is_a_release_of_its_own(tmp_path: Path) -> None:
    found = await artifacts(library(tmp_path, {}), None)
    indexed = by_id(found)
    assert found.uses == [("Nat.add", "Nat")]
    assert indexed["Nat"].key == "Nat"
    assert indexed["Nat"].document.startswith("Base\ntype Nat")
    assert indexed["Nat.add"].signature is not None


async def test_features_describe_each_definition(tmp_path: Path) -> None:
    main = (
        "import Base\n\ndef put(n: Nat) -> Nat:\n  put.go(n)\n\ndef put.go(n: Nat) -> Nat:\n  n\n"
    )
    found = await artifacts(
        library(tmp_path, {LIB: (LIB_HASH, {"main.bend": main, "box.bend": BOX})}), LIB_HASH
    )
    indexed = by_id(found)
    assert indexed["lib@1.0.0.0/main:put.go"].helper
    assert not indexed["lib@1.0.0.0/main:put"].helper
    assert indexed["lib@1.0.0.0/box:open"].documented
    assert indexed["lib@1.0.0.0/main:put"].key == "lib/main:put"
    assert indexed["lib@1.0.0.0/main:put"].document.startswith("lib main\ndef put")


BROKEN = "import Base\n\ntype Nat is Data:\n  Zero{}\n"


async def test_a_file_that_does_not_load_is_reported_with_its_line(tmp_path: Path) -> None:
    found = await artifacts(library(tmp_path, {LIB: (LIB_HASH, {"main.bend": BROKEN})}), LIB_HASH)
    error = found.errors["main.bend"]
    assert error.expected == "a fresh name (duplicate declaration: Nat)"
    assert error.place == Place("lib@1.0.0.0/main.bend", 3)


async def test_a_file_that_imports_a_broken_file_names_that_file(tmp_path: Path) -> None:
    main = "import Base\nimport other@1.0.0.0/broken.bend as B\n\ndef root(x: Nat) -> Nat:\n  x\n"
    found = await artifacts(
        library(
            tmp_path,
            {
                LIB: (LIB_HASH, {"main.bend": main, "box.bend": BOX}),
                OTHER: (OTHER_HASH, {"broken.bend": BROKEN}),
            },
        ),
        LIB_HASH,
    )
    assert list(found.errors) == ["main.bend"]
    assert found.errors["main.bend"].place == Place("other@1.0.0.0/broken.bend", 3)


async def test_a_missing_indexer_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolve, "INDEXER", "jendhub-index-missing")
    with pytest.raises(UsesError, match="not on PATH"):
        await artifacts(library(tmp_path, {LIB: (LIB_HASH, {"box.bend": BOX})}), LIB_HASH)


async def test_a_def_that_fills_a_law_is_no_definition_on_either_side(
    tmp_path: Path,
) -> None:
    law = "import Base\n\nlaw unit:\n  Nat\n\ndef unit() -> Nat:\n  Zero{}\n"
    found = await artifacts(library(tmp_path, {LIB: (LIB_HASH, {"main.bend": law})}), LIB_HASH)
    assert found.mismatches == {}
    assert [indexed.definition.name for indexed in found.definitions] == ["unit"]
