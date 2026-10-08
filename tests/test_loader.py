import pytest

from jend.loader import Definition, Import, Library, import_lines
from jend.mirror import File, extract
from jend.parser import Kind

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
    return extract(library, [BASE, *sources])


def _one(text: str) -> dict[str, Definition]:
    file = _files({(PACKAGE, "a.bend"): text})[(PACKAGE, "a.bend")]
    assert file.error is None
    return {definition.name: definition for definition in file.definitions}


def _key(name: str, path: str = "a") -> str:
    return f"{PACKAGE}/{path}:{name}"


def test_a_local_name_does_not_reference_the_definition_with_that_name() -> None:
    found = _one(
        "import Base\n\n"
        "def bits(x: U32) -> U32:\n  x\n\n"
        "def shadowed(bits: U32) -> U32:\n  bits\n\n"
        "def caller(x: U32) -> U32:\n  bits(x)\n"
    )

    assert found["shadowed"].refs == ("U32",)
    assert found["caller"].refs == (_key("bits"), "U32")


def test_field_names_are_not_references() -> None:
    found = _one(
        "import Base\n\ndef size(x: U32) -> U32:\n  x\n\ntype Box is Data:\n  Box{size: U32}\n"
    )

    assert found["Box"].refs == ("U32",)


def test_operators_resolve_to_the_method_of_the_annotated_type() -> None:
    found = _one("import Base\n\ndef inc(x: U32) -> U32:\n  (x + 1 : U32)\n")

    assert found["inc"].refs == ("U32", "U32.add")


def test_constructors_reference_their_type() -> None:
    found = _one("import Base\n\ndef wrap(x: U32) -> Maybe<&2, U32>:\n  Some{x}\n")

    assert found["wrap"].refs == ("Maybe", "U32")


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

    assert hashed.imports == (Import("./core.bend", "Core", f"{PACKAGE}/src/core"),)
    assert hashed.definitions[0].refs == (_key("digest", "src/core"), "U32")


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

    assert files[(PACKAGE, "a.bend")].definitions[0].refs == (f"{OTHER}/zlib:inflate", "U32")


@pytest.mark.parametrize(
    "text",
    [
        "import Base\n\ndef Date.leap.100(x: U32) -> U32:\n  x\n",
        "import Base\n\ndef inc(x: U32) -> U32:\n  x + 1\n",
        "import Base\n\ndef f(x: U32) -> U32:\n  x\n\ndef f(y: U32) -> U32:\n  y\n",
        "import ./missing.bend as Missing\n",
    ],
)
def test_bend_rejects_the_file(text: str) -> None:
    file = _files({(PACKAGE, "a.bend"): text})[(PACKAGE, "a.bend")]

    assert file.error is not None
    assert file.definitions == ()


def test_a_file_that_imports_a_rejected_file_is_rejected() -> None:
    files = _files(
        {
            (PACKAGE, "bad.bend"): "import Base\n\ndef bad.1(x: U32) -> U32:\n  x\n",
            (PACKAGE, "a.bend"): "import Base\nimport ./bad.bend as Bad\n",
        }
    )

    assert files[(PACKAGE, "a.bend")].error is not None


def test_names_without_import_base_stay_in_the_file() -> None:
    assert _one("def f(x: Nat) -> Nat:\n  x\n")["f"].refs == ()
    assert _one("import Base\n\ndef f(x: Nat) -> Nat:\n  x\n")["f"].refs == ("Nat",)


def test_a_proof_references_apart_from_the_statement_of_its_law() -> None:
    found = _one(
        "import Base\n\n"
        "def id(x: U32) -> U32:\n  x\n\n"
        "def helper(x: U32) -> U32:\n  x\n\n"
        "law id_same:\n  for x: U32\n  {id(x) == x : U32}\n\n"
        "def id_same(x):\n  helper(x)\n"
    )

    assert found["id_same"].kind is Kind.LAW
    assert found["id_same"].refs == (_key("id"), "U32")
    assert found["id_same"].proof_refs == (_key("helper"),)
    assert list(found) == ["id", "helper", "id_same"]


def test_signatures_docs_and_spans() -> None:
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

    assert wrap.signature == "@unsafe def wrap(x: U32) -> Maybe<&2, U32>"
    assert wrap.doc == "Wraps a value. Second line."
    assert (wrap.line, wrap.first_line, wrap.last_line) == (6, 2, 8)
    assert pair.signature == "type Pair is Data:\n  # the two halves\n  Pair{fst: U32, snd: U32}"
    assert pair.doc == "Comment of the next one."
    assert (pair.line, pair.first_line, pair.last_line) == (12, 10, 13)


def test_import_lines_stop_at_the_first_declaration() -> None:
    text = "# header\nimport Base\nimport ./a.bend as A  # note\n\ndef f() -> U32:\n  0\nimport ./b.bend as B\n"

    assert [(line.index, line.target, line.alias) for line in import_lines(text)] == [
        (1, "Base", None),
        (2, "./a.bend", "A"),
    ]
