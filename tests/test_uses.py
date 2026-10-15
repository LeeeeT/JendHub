from pathlib import Path

import pytest

from jendhub import uses
from jendhub.definitions import Corpus, corpus_definitions
from jendhub.hub import Listing, Package, Release
from jendhub.mirror import Mirror
from jendhub.uses import UseGraph, UsesError, use_graph

LIB = Release("lib", "1.0.0.0")
OTHER = Release("other", "1.0.0.0")
LIB_HASH = "0x" + "a" * 32
OTHER_HASH = "0x" + "b" * 32

BASE = """\
type Nat is Data:
  Zero{}
  Succ{pred: Nat}

def Nat.add(a: Nat, b: Nat) -> Nat:
  a
"""

BOX = """\
import Base

type Box is Data:
  Box{value: Nat}

def open(b: Box) -> Nat:
  match b:
    case Box{v}:
      v
"""


def mirror(root: Path, packages: dict[Release, tuple[str, dict[str, str]]]) -> Mirror:
    full = Mirror(root)
    full.base_source.parent.mkdir(parents=True)
    full.base_source.write_text(BASE)
    for release, (hash_, files) in packages.items():
        encoded = {path: text.encode() for path, text in files.items()}
        full.store_package(Package(Listing(hash_, release), encoded))
    return full


def graph(full: Mirror, releases: list[Release]) -> UseGraph:
    corpus = Corpus(full, releases)
    return use_graph(corpus, full, corpus_definitions(corpus))


def test_uses_cross_the_modules_of_a_package(tmp_path: Path) -> None:
    main = "import Base\nimport ./box.bend as B\n\ndef root(x: B.Box) -> Nat:\n  B.open(x)\n"
    full = mirror(tmp_path, {LIB: (LIB_HASH, {"box.bend": BOX, "main.bend": main})})
    found = graph(full, [LIB])
    assert found.users["lib@1.0.0.0/box:open"] == {"lib@1.0.0.0/main:root"}
    assert found.used["lib@1.0.0.0/main:root"] == {
        "lib@1.0.0.0/box:Box",
        "lib@1.0.0.0/box:open",
    }
    assert found.used["lib@1.0.0.0/box:open"] == {"lib@1.0.0.0/box:Box"}
    assert found.errors == {}


def test_a_local_shadows_a_declaration(tmp_path: Path) -> None:
    main = (
        "import Base\n\ndef name(n: Nat) -> Nat:\n  n\n\n"
        "def root(x: Nat) -> Nat:\n  name = x\n  name\n"
    )
    full = mirror(tmp_path, {LIB: (LIB_HASH, {"main.bend": main})})
    assert "lib@1.0.0.0/main:root" not in graph(full, [LIB]).used


def test_base_and_other_packages_are_not_uses(tmp_path: Path) -> None:
    main = (
        "import Base\nimport other@1.0.0.0/box.bend as B\n\n"
        "def root(x: Nat) -> Nat:\n  (B.open(B.Box{x}) + Zero{} : Nat)\n"
    )
    full = mirror(
        tmp_path,
        {LIB: (LIB_HASH, {"main.bend": main}), OTHER: (OTHER_HASH, {"box.bend": BOX})},
    )
    found = graph(full, [LIB])
    assert "lib@1.0.0.0/main:root" not in found.used
    assert found.users == {"Nat": {"Nat.add"}}
    assert found.errors == {}


def test_a_file_that_does_not_load_is_reported(tmp_path: Path) -> None:
    broken = "import Base\n\ntype Nat is Data:\n  Zero{}\n"
    full = mirror(tmp_path, {LIB: (LIB_HASH, {"main.bend": broken})})
    found = graph(full, [LIB])
    assert found.errors == {"lib@1.0.0.0/main": "a fresh name (duplicate declaration: Nat)"}


def test_a_missing_indexer_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = mirror(tmp_path, {LIB: (LIB_HASH, {"box.bend": BOX})})
    monkeypatch.setattr(uses, "INDEXER", "jendhub-index-missing")
    with pytest.raises(UsesError, match="not on PATH"):
        graph(full, [LIB])
