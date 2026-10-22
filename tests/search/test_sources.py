import pytest

from jendhub.search.sources import (
    SourceFile,
    alias,
    call_name,
    file_path,
    import_line,
    imported_hashes,
    used_imports,
)

HASH = "0x" + "a" * 32


@pytest.mark.parametrize(
    ("package", "module", "expected"),
    [
        ("bend-kit-json", "json", "Json"),
        ("bend-kit-json", "main", "BendKitJson"),
        ("kit", "src/byte_order", "ByteOrder"),
        ("kit", "lib/u32div", "U32div"),
        ("kit", "src/2d", "M2d"),
    ],
)
def test_the_alias_is_the_module_name(package: str, module: str, expected: str) -> None:
    assert alias(package, module) == expected


def test_a_package_definition_is_imported_by_its_exact_release() -> None:
    assert import_line("kit", "1.0.0.0", "src/json") == "import kit@1.0.0.0/src/json.bend as Json"
    assert call_name("parse.bytes", "kit", "src/json") == "Json.parse.bytes"
    assert file_path("kit", "1.0.0.0", "src/json") == "kit@1.0.0.0/src/json.bend"


def test_a_base_definition_needs_only_base() -> None:
    assert import_line(None, None, "") == "import Base"
    assert call_name("List.map", None, "") == "List.map"
    assert file_path(None, None, "") == "Base/base.bend"


def test_imports_name_exact_releases() -> None:
    text = (
        "import Base\n"
        "import ../lib/nat.bend as N\n"
        "import ./state.bend as ST\n"
        f"import {HASH}/bytes.bend as Bytes\n"
        "import other@2.0.0.0/x.bend as X\n"
        "def f(n: Nat) -> Nat:\n"
        "  n\n"
    )
    file = SourceFile("kit@1.0.0.0", HASH, "src/a/main.bend", text, {HASH: "bytes@0.3.0.0"})
    assert "\n".join(file.shown(line) for line in text.split("\n")) == (
        "import Base\n"
        "import kit@1.0.0.0/src/lib/nat.bend as N\n"
        "import kit@1.0.0.0/src/a/state.bend as ST\n"
        "import bytes@0.3.0.0/bytes.bend as Bytes\n"
        "import other@2.0.0.0/x.bend as X\n"
        "def f(n: Nat) -> Nat:\n"
        "  n\n"
    )


def test_an_import_outside_the_package_or_of_an_unnamed_hash_stays() -> None:
    text = f"import ../../up.bend as U\nimport {HASH}/bytes.bend as Bytes"
    file = SourceFile("kit@1.0.0.0", HASH, "main.bend", text, {})
    assert [file.shown(line) for line in text.split("\n")] == text.split("\n")
    assert imported_hashes(text) == [HASH]


def test_a_definition_keeps_only_the_imports_that_its_code_uses() -> None:
    file = (
        "import Base\n"
        "import kit@1.0.0.0/nat.bend as N\n"
        "import kit@1.0.0.0/list.bend as L\n"
        "import kit@1.0.0.0/lazy.bend as NL\n"
    )
    code = "def f(xs: L.List) -> Nat:\n  N.add(NL.force(xs), Main.N.x)"
    assert used_imports(file, code) == [0, 1, 2, 3]
    assert used_imports(file, "def g() -> Nat:\n  Main.N.x") == [0]


def test_a_repeated_import_line_is_used_once() -> None:
    file = "import Base\nimport kit@1.0.0.0/nat.bend as N\nimport kit@1.0.0.0/nat.bend as N  \n"
    assert used_imports(file, "def f() -> Nat:\n  N.zero") == [0, 1]
