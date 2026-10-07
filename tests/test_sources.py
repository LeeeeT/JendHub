from datetime import UTC, datetime
from pathlib import Path

from jend.mirror import File, Mirror, Package
from jend.sources import Sources, label, write
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
    write(
        tmp_path / "index", Mirror(base=base, packages=(cold, hot)), [base, hot], tmp_path / "files"
    )
    return Sources(tmp_path / "index")


def test_listing_shows_only_the_listed_packages_in_their_order(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert source_text(sources, "", None) == (
        f"Base@1.0.0.0: About Base.\n0x{'b' * 32}: About None."
    )
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
