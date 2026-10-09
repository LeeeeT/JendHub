from datetime import UTC, datetime
from pathlib import Path

from jend.loader import Library
from jend.mirror import Base, File, Mirror, Named, Package, extract, label
from jend.sources import Sources, write
from jend.web import source_text

PUBLISHED = datetime(2026, 1, 1, tzinfo=UTC)
BASE = "c" * 40
COLD = "0x" + "a" * 32
HOT = "0x" + "b" * 32
ORIGIN = "https://jend.test"
BASE_TEXT = "type U32 is Data:\n  U32{}\n\ndef U32.add(a: U32, b: U32) -> U32:\n  a\n"
TEXT = (
    "import Base\n\n"
    "# Adds one.\ndef inc(x: U32) -> U32:\n  (x + 1 : U32)\n\n"
    "def dec(x: U32) -> U32:\n  x\n"
)
SOURCES = {
    (BASE, "base.bend"): BASE_TEXT,
    (COLD, "a.bend"): TEXT,
    (HOT, "src/core.bend"): TEXT,
    (HOT, "src/math.bend"): (
        "import Base\n"
        "import ./core.bend as Core\n"
        "import cold@1.0.0.0/a.bend as Cold\n\n"
        "def twice(x: U32) -> U32:\n  Core.inc(Core.inc(x))\n"
    ),
    (HOT, "main.bend"): "import Base\n\ndef main() -> U32:\n  0\n",
    (HOT, "bad.bend"): "import Base\n\ndef bad(x: U32) -> U32:\n  x + 1\n",
    (HOT, "src/laws.bend"): (
        "import Base\n\n"
        "law same:\n  for x: U32\n  {x == x : U32}\n\n"
        "def same(x):\n  {==}\n\n"
        "law kept:\n  for x: U32\n  {x == x : U32}\n"
    ),
    (HOT, "src/proofs.bend"): (
        "import Base\n"
        "import ./core.bend as Core\n"
        "import ./laws.bend as Laws\n"
        "import cold@1.0.0.0/a.bend as Cold\n\n"
        "# Proves kept.\n"
        "def Laws.kept(x):\n  Core.inc(x)\n"
    ),
}


def _package(hash: str, name: str | None, files: tuple[File, ...]) -> Package:
    return Package(
        hash=hash,
        label=None if name is None else Named(name=name, version="1.0.0.0"),
        description=f"About {name}.",
        published=PUBLISHED,
        hot=1.0,
        files=files,
    )


def _sources(tmp_path: Path) -> Sources:
    library = Library(SOURCES, {"cold@1.0.0.0": COLD}, (BASE, "base.bend"))
    files = extract(library, list(SOURCES), BASE)

    def owned(package: str) -> tuple[File, ...]:
        return tuple(file for (owner, _), file in files.items() if owner == package)

    mirror = Mirror(
        base=Base(commit=BASE, file=files[(BASE, "base.bend")]),
        packages=(_package(COLD, "cold", owned(COLD)), _package(HOT, None, owned(HOT))),
    )
    (tmp_path / "index").mkdir()
    write(tmp_path / "index", mirror)
    return Sources(tmp_path / "index")


def test_no_package_is_not_found(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "", None, ORIGIN) is None
    sources.close()


def test_packages_resolve_by_label_or_hash(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "cold@1.0.0.0", None, ORIGIN) == "a.bend"
    assert source_text(sources, f"{COLD}/", None, ORIGIN) == "a.bend"
    assert source_text(sources, f"{HOT}/", None, ORIGIN) == (
        "main.bend\nsrc/core.bend\nsrc/laws.bend\nsrc/math.bend\nsrc/proofs.bend"
    )
    assert source_text(sources, "cold@2.0.0.0/", None, ORIGIN) is None
    sources.close()


def test_a_file_that_bend_rejects_is_not_served(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, f"{HOT}/bad.bend", None, ORIGIN) is None
    sources.close()


def test_a_file_or_one_definition_with_its_doc_comment(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "cold@1.0.0.0/a.bend", None, ORIGIN) == TEXT
    assert source_text(sources, "cold@1.0.0.0/a.bend", "inc", ORIGIN) == (
        "import Base\n\n# Adds one.\ndef inc(x: U32) -> U32:\n  (x + 1 : U32)"
    )
    assert source_text(sources, "cold@1.0.0.0/a.bend", "missing", ORIGIN) is None
    assert source_text(sources, "cold@1.0.0.0/b.bend", None, ORIGIN) is None
    sources.close()


def test_import_lines_become_package_paths(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert sources.text(HOT, "src/math.bend") == (
        "import Base\n"
        f"import {HOT}/src/core.bend as Core\n"
        "import cold@1.0.0.0/a.bend as Cold\n\n"
        "def twice(x: U32) -> U32:\n  Core.inc(Core.inc(x))\n"
    )
    sources.close()


def test_a_definition_starts_with_the_imports_that_it_uses(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, f"{HOT}/src/math.bend", "twice", ORIGIN) == (
        f"import Base\nimport {HOT}/src/core.bend as Core\n\n"
        "def twice(x: U32) -> U32:\n  Core.inc(Core.inc(x))"
    )
    sources.close()


def test_a_definition_of_base_does_not_import_base(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, f"{BASE}/base.bend", "U32.add", ORIGIN) == (
        "def U32.add(a: U32, b: U32) -> U32:\n  a"
    )
    sources.close()


def test_a_law_comes_with_its_fill_from_the_same_file(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, f"{HOT}/src/laws.bend", "same", ORIGIN) == (
        "import Base\n\nlaw same:\n  for x: U32\n  {x == x : U32}\n\ndef same(x):\n  {==}"
    )
    sources.close()


def test_a_fill_from_another_file_follows_with_its_url(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, f"{HOT}/src/laws.bend", "kept", ORIGIN) == (
        "import Base\n\nlaw kept:\n  for x: U32\n  {x == x : U32}\n\n"
        f"# {ORIGIN}/src/{HOT}/src/proofs.bend\n"
        f"import {HOT}/src/core.bend as Core\n"
        f"import {HOT}/src/laws.bend as Laws\n\n"
        "# Proves kept.\ndef Laws.kept(x):\n  Core.inc(x)"
    )
    sources.close()


def test_label_is_name_at_version_the_hash_or_the_commit_of_base() -> None:
    assert label(_package("0x1", "zlib", ())) == "zlib@1.0.0.0"
    assert label(_package("0x1", None, ())) == "0x1"
    base_file = File(path="base.bend", text="", imports=(), tlds=())
    assert label(Base(commit=BASE, file=base_file)) == BASE
