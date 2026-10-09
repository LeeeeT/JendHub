from pathlib import Path

import pytest

from jend.hub import Listing
from jend.loader import Library, import_lines
from jend.mirror import (
    Adt,
    Def,
    File,
    Fill,
    Import,
    Law,
    Named,
    PackageFile,
    Tld,
    build,
    extract,
)

BASE = ("c" * 40, "base.bend")
PACKAGE = "0x" + "a" * 32
OTHER = "0x" + "b" * 32
BASE_TEXT = """\
type Bool is Data:
  False{}
  True{}

type Nat is Data:
  Zero{}
  Succ{pred: Nat}

type U32 is Data:
  U32{bits: Nat}

def U32.add(a: U32, b: U32) -> U32:
  a

type Maybe<-A: Data> is Data:
  None{}
  Some{value: A}
"""


def _files(
    sources: dict[tuple[str, str], str], names: dict[str, str] | None = None
) -> dict[tuple[str, str], File]:
    library = Library({BASE: BASE_TEXT, **sources}, names or {}, BASE)
    return extract(library, [BASE, *sources], BASE[0])


def _one(text: str) -> dict[str, Tld]:
    file = _files({(PACKAGE, "a.bend"): text})[(PACKAGE, "a.bend")]
    return {tld.name: tld for tld in file.tlds}


def _refs(tld: Tld) -> list[str]:
    return [key.text() for key in tld.refs]


def _key(name: str, path: str = "a") -> str:
    return f"{PACKAGE}/{path}:{name}"


def test_a_local_name_does_not_reference_the_definition_with_that_name() -> None:
    found = _one(
        "import Base\n\n"
        "def bits(x: U32) -> U32:\n  x\n\n"
        "def shadowed(bits: U32) -> U32:\n  bits\n\n"
        "def caller(x: U32) -> U32:\n  bits(x)\n"
    )

    assert _refs(found["shadowed"]) == ["U32"]
    assert _refs(found["caller"]) == [_key("bits"), "U32"]


def test_field_names_are_not_references() -> None:
    found = _one(
        "import Base\n\ndef size(x: U32) -> U32:\n  x\n\ntype Box is Data:\n  Box{size: U32}\n"
    )

    assert _refs(found["Box"]) == ["U32"]


def test_operators_resolve_to_the_method_of_the_annotated_type() -> None:
    found = _one("import Base\n\ndef inc(x: U32) -> U32:\n  (x + 1 : U32)\n")

    assert _refs(found["inc"]) == ["U32", "U32.add"]


def test_constructors_reference_their_type() -> None:
    found = _one("import Base\n\ndef wrap(x: U32) -> Maybe<&2, U32>:\n  Some{x}\n")

    assert _refs(found["wrap"]) == ["Maybe", "U32"]


def test_an_adt_does_not_reference_the_constructors_that_it_declares() -> None:
    found = _one(
        "type Maybe<a, -A: Kind(a)> is Kind(a):\n  None{}\n  Some{value: A}\n\n"
        "type Nat is Data:\n  Zero{}\n  Succ{pred: Nat}\n"
    )

    assert _refs(found["Maybe"]) == []
    assert _refs(found["Nat"]) == [_key("Nat")]


def test_a_recursive_def_references_itself() -> None:
    found = _one(
        "import Base\n\n"
        "def count(n: Nat) -> Nat:\n  match n:\n    case Zero{}:\n      Zero{}\n"
        "    case Succ{p}:\n      count(p)\n"
    )

    assert _refs(found["count"]) == [_key("count"), "Nat"]


def test_aliases_resolve_to_the_imported_file() -> None:
    files = _files(
        {
            (PACKAGE, "src/core.bend"): "import Base\n\ndef digest(b: U32) -> U32:\n  b\n",
            (PACKAGE, "src/hash.bend"): (
                "import Base\nimport ./core.bend as Core\n\n"
                "def hash(b: U32) -> U32:\n  Core.digest(b)\n"
            ),
        }
    )
    hashed = files[(PACKAGE, "src/hash.bend")]

    assert hashed.imports == (Import(alias="Core", file=PackageFile(PACKAGE, "src/core.bend")),)
    assert _refs(hashed.tlds[0]) == [_key("digest", "src/core"), "U32"]


def test_named_imports_resolve_through_the_hub_names() -> None:
    files = _files(
        {
            (OTHER, "zlib.bend"): "import Base\n\ndef inflate(b: U32) -> U32:\n  b\n",
            (PACKAGE, "a.bend"): (
                "import Base\nimport zlib@1.0.0.0/zlib.bend as Zlib\n\n"
                "def unzip(b: U32) -> U32:\n  Zlib.inflate(b)\n"
            ),
        },
        {"zlib@1.0.0.0": OTHER},
    )

    assert _refs(files[(PACKAGE, "a.bend")].tlds[0]) == [f"{OTHER}/zlib:inflate", "U32"]


@pytest.mark.parametrize(
    "text",
    [
        "import Base\n\ndef Date.leap.100(x: U32) -> U32:\n  x\n",
        "import Base\n\ndef inc(x: U32) -> U32:\n  x + 1\n",
        "import Base\n\ndef f(x: U32) -> U32:\n  x\n\ndef f(y: U32) -> U32:\n  y\n",
        "import ./missing.bend as Missing\n",
    ],
)
def test_a_file_that_bend_rejects_is_left_out(text: str) -> None:
    assert (PACKAGE, "a.bend") not in _files({(PACKAGE, "a.bend"): text})


def test_a_file_that_imports_a_rejected_file_is_left_out() -> None:
    files = _files(
        {
            (PACKAGE, "bad.bend"): "import Base\n\ndef bad.1(x: U32) -> U32:\n  x\n",
            (PACKAGE, "a.bend"): "import Base\nimport ./bad.bend as Bad\n",
        }
    )

    assert (PACKAGE, "a.bend") not in files


def test_names_without_import_base_stay_in_the_file() -> None:
    assert _refs(_one("def f(x: Nat) -> Nat:\n  x\n")["f"]) == []
    assert _refs(_one("import Base\n\ndef f(x: Nat) -> Nat:\n  x\n")["f"]) == ["Nat"]


def test_a_law_references_what_its_statement_and_its_fill_use() -> None:
    found = _one(
        "import Base\n\n"
        "def id(x: U32) -> U32:\n  x\n\n"
        "def helper(x: U32) -> U32:\n  x\n\n"
        "law id_same:\n  for x: U32\n  {id(x) == x : U32}\n\n"
        "def id_same(x):\n  helper(x)\n"
    )

    assert isinstance(found["id_same"].kind, Law)
    assert _refs(found["id_same"]) == [_key("helper"), _key("id"), "U32"]
    assert list(found) == ["id", "helper", "id_same"]


def test_a_law_keeps_the_doc_and_the_code_of_its_fill() -> None:
    law = _one(
        "import Base\n\n"
        "# States it.\n"
        "law same:\n  for x: U32\n  {x == x : U32}\n\n"
        "# Proves it.\n"
        "def same(x):\n  {==}\n"
    )["same"]

    assert law.doc == "# States it."
    assert law.code == "law same:\n  for x: U32\n  {x == x : U32}"
    assert law.kind == Law(
        fill=Fill(
            file=PackageFile(PACKAGE, "a.bend"), doc="# Proves it.", code="def same(x):\n  {==}"
        )
    )
    assert law.full_doc == "States it. Proves it."


def test_a_law_keeps_a_fill_from_another_file() -> None:
    files = _files(
        {
            (PACKAGE, "a.bend"): "import Base\n\nlaw same:\n  for x: U32\n  {x == x : U32}\n",
            (PACKAGE, "proof.bend"): (
                "import Base\nimport ./a.bend as A\n\ndef A.same(x):\n  {==}\n"
            ),
        }
    )

    (law,) = files[(PACKAGE, "a.bend")].tlds
    assert law.kind == Law(
        fill=Fill(file=PackageFile(PACKAGE, "proof.bend"), doc="", code="def A.same(x):\n  {==}")
    )
    assert files[(PACKAGE, "proof.bend")].tlds == ()


def test_signatures_docs_and_code() -> None:
    found = _one(
        "import Base\n\n"
        "# Wraps a value.\n"
        "# Second line.\n"
        "@unsafe\n"
        "def wrap(\n  x: U32  # the value\n) -> Maybe<&2, U32>:\n  Some{x}\n\n"
        "# Comment of the next one.\n"
        "type Pair is Data:\n"
        "  # the two halves\n"
        "  Pair{fst: U32, snd: U32}\n"
    )
    wrap, pair = found["wrap"], found["Pair"]

    assert wrap.kind == Def(signature="@unsafe def wrap(x: U32) -> Maybe<&2, U32>")
    assert wrap.doc == "# Wraps a value.\n# Second line."
    assert wrap.code == "@unsafe\ndef wrap(\n  x: U32  # the value\n) -> Maybe<&2, U32>:\n  Some{x}"
    assert wrap.declaration == "@unsafe def wrap(x: U32) -> Maybe<&2, U32>"
    assert wrap.full_doc == "Wraps a value. Second line."
    assert pair.kind == Adt()
    assert pair.code == "type Pair is Data:\n  # the two halves\n  Pair{fst: U32, snd: U32}"
    assert pair.declaration == pair.code
    assert pair.doc == "# Comment of the next one."


def test_texts_use_line_feeds() -> None:
    files = _files(
        {(PACKAGE, "a.bend"): "import Base\r\n\r\n# Zero.\r\ndef z() -> U32:\r\n  0\r\n"}
    )
    file = files[(PACKAGE, "a.bend")]

    assert file.text == "import Base\n\n# Zero.\ndef z() -> U32:\n  0\n"
    assert (file.tlds[0].doc, file.tlds[0].code) == ("# Zero.", "def z() -> U32:\n  0")


def test_the_mirror_leaves_out_a_package_without_an_accepted_file(tmp_path: Path) -> None:
    texts = {
        BASE: BASE_TEXT,
        (PACKAGE, "a.bend"): "import Base\n\ndef f(x: U32) -> U32:\n  x\n",
        (OTHER, "a.bend"): "import Base\n\ndef f(x: U32) -> U32:\n  x + 1\n",
    }
    for (folder, path), text in texts.items():
        (tmp_path / folder).mkdir()
        (tmp_path / folder / path).write_text(text, encoding="utf-8")
    listings = [
        Listing(
            hash=PACKAGE,
            name="good",
            version="1.0.0.0",
            desc="",
            ts=0,
            hot=1.0,
            files={"a.bend": 1},
        ),
        Listing(hash=OTHER, name=None, version=None, desc="", ts=0, hot=1.0, files={"a.bend": 1}),
    ]

    mirror = build(BASE[0], listings, tmp_path)

    assert [package.hash for package in mirror.packages] == [PACKAGE]
    assert mirror.packages[0].label == Named(name="good", version="1.0.0.0")
    assert mirror.base.file.path == "base.bend"


def test_import_lines_stop_at_the_first_declaration() -> None:
    text = "# header\nimport Base\nimport ./a.bend as A  # note\n\ndef f() -> U32:\n  0\nimport ./b.bend as B\n"

    assert [(line.index, line.target, line.alias) for line in import_lines(text)] == [
        (1, "Base", None),
        (2, "./a.bend", "A"),
    ]
