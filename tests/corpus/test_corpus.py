from pathlib import Path

from jendhub.corpus.corpus import Corpus, corpus_text
from jendhub.hub.api import Listing, Package, PackageRelease, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library

KIT = "0x" + "a" * 32
EXTRA = "0x" + "b" * 32


def corpus(root: Path) -> Corpus:
    library = Library(root)
    library.store_base(Base("v2.0.38", b"def List.map(xs: List) -> List:\n  xs\n"))
    files = {"json.bend": b"def parse(s: String) -> Json:\n  s\n"}
    library.store_package(Package(Listing(KIT, Release("kit", "0.1.0.0")), files))
    extra = {"extra.bend": b"def extra(n: Nat) -> Nat:\n  n\n"}
    library.store_package(Package(Listing(EXTRA, Release("extra", "0.1.0.0")), extra))
    return Corpus(library, "v2.0.38", [PackageRelease(KIT, Release("kit", "0.1.0.0"))])


def test_corpus_text_heads_each_file_with_its_id_prefix(tmp_path: Path) -> None:
    text = corpus_text(corpus(tmp_path))
    assert text.startswith("===== Base =====\ndef List.map")
    assert "===== kit@0.1.0.0/json =====\ndef parse" in text


def test_corpus_text_holds_only_the_releases_of_the_corpus(tmp_path: Path) -> None:
    assert "extra" not in corpus_text(corpus(tmp_path))
