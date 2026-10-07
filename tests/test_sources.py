from datetime import UTC, datetime
from pathlib import Path

from jend.mirror import File, Mirror, Package
from jend.sources import Sources, absolute_imports, label, write
from jend.web import source_text

PUBLISHED = datetime(2026, 1, 1, tzinfo=UTC)
TEXT = "# Adds one.\ndef inc(x: U32) -> U32:\n  x + 1\n\ndef dec(x: U32) -> U32:\n  x - 1\n"


def _package(hash: str, name: str | None, hot: float | None, paths: tuple[str, ...]) -> Package:
    return Package(
        hash=hash,
        name=name,
        version=None if name is None else "1.0.0.0",
        description=f"About {name}.",
        published=PUBLISHED,
        hot=hot,
        files=tuple(File(path=path, definitions=(), unparsed_lines=()) for path in paths),
    )


def _sources(tmp_path: Path) -> Sources:
    base = _package("c" * 40, "Base", None, ("base.bend",))
    cold = _package("0x" + "a" * 32, "cold", 1.0, ("a.bend",))
    hot = _package("0x" + "b" * 32, None, 2.0, ("src/math.bend", "main.bend"))
    for package in (base, cold, hot):
        for file in package.files:
            path = tmp_path / "files" / package.hash / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEXT, encoding="utf-8")
    (tmp_path / "index").mkdir()
    write(tmp_path / "index", Mirror(base=base, packages=(cold, hot)), tmp_path / "files")
    return Sources(tmp_path / "index")


def test_no_package_is_not_found(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "", None) is None
    sources.close()


def test_packages_resolve_by_label_or_hash(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "cold@1.0.0.0", None) == "a.bend"
    assert source_text(sources, f"0x{'a' * 32}/", None) == "a.bend"
    assert source_text(sources, f"0x{'b' * 32}/", None) == "main.bend\nsrc/math.bend"
    assert source_text(sources, "cold@2.0.0.0/", None) is None
    sources.close()


def test_a_file_or_one_definition_with_its_doc_comment(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "cold@1.0.0.0/a.bend", None) == TEXT
    assert source_text(sources, "cold@1.0.0.0/a.bend", "inc") == (
        "# Adds one.\ndef inc(x: U32) -> U32:\n  x + 1"
    )
    assert source_text(sources, "cold@1.0.0.0/a.bend", "missing") is None
    assert source_text(sources, "cold@1.0.0.0/b.bend", None) is None
    sources.close()


def test_label_is_name_at_version_or_the_hash() -> None:
    assert label(_package("0x1", "zlib", 1.0, ())) == "zlib@1.0.0.0"
    assert label(_package("0x1", None, 1.0, ())) == "0x1"


def test_relative_imports_become_package_paths() -> None:
    text = (
        "import Base\n"
        "import ./sha/core.bend as Core\n"
        "import ../util.bend as Util\n"
        "import ../../../outside.bend as Out\n"
        "import zlib@1.0.0.0/zlib.bend as Zlib\n"
    )
    assert absolute_imports("p@1.0.0.0", "src/crypto/hash.bend", text) == (
        "import Base\n"
        "import p@1.0.0.0/src/crypto/sha/core.bend as Core\n"
        "import p@1.0.0.0/src/util.bend as Util\n"
        "import ../../../outside.bend as Out\n"
        "import zlib@1.0.0.0/zlib.bend as Zlib\n"
    )


def test_a_definition_starts_with_the_imports_that_it_uses(tmp_path: Path) -> None:
    text = (
        "import Base\n"
        "import ./core.bend as Core\n"
        "import ./other.bend as Other\n\n"
        "def hash(b: Bytes) -> Bytes:\n"
        "  Core.digest(b)\n"
    )
    package = _package("0x" + "a" * 32, "p", 1.0, ("src/hash.bend",))
    file = tmp_path / "files" / package.hash / "src" / "hash.bend"
    file.parent.mkdir(parents=True)
    file.write_text(text, encoding="utf-8")
    (tmp_path / "index").mkdir()
    base = _package("c" * 40, "Base", None, ())
    write(tmp_path / "index", Mirror(base=base, packages=(package,)), tmp_path / "files")
    sources = Sources(tmp_path / "index")

    assert sources.definition("p@1.0.0.0", "src/hash.bend", "hash") == (
        "import p@1.0.0.0/src/core.bend as Core\n\ndef hash(b: Bytes) -> Bytes:\n  Core.digest(b)"
    )
    sources.close()
