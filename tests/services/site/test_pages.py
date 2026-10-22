from jendhub.search.definitions import DefinitionDetail, ImportLine
from jendhub.search.highlights import Highlight, Kind
from jendhub.search.sources import Declared, SourceFile
from jendhub.services.site.pages import (
    Link,
    Piece,
    Run,
    SourceLine,
    body_lines,
    file_lines,
    head_lines,
    import_lines,
    import_link,
    lines_href,
    reference,
    shortened,
    source_line,
    styled,
)

HASH = "0x" + "a" * 32


def mark(start: int, end: int, kind: Kind, *, declaration: bool = False) -> Highlight:
    return Highlight(start, end, kind, declaration)


def test_an_import_line_links_to_its_file() -> None:
    assert import_link("import kit@1.0.0.0/deep/odd.bend as Odd") == Link(
        "import ", "kit@1.0.0.0/deep/odd.bend", "/files/kit@1.0.0.0/deep/odd.bend", " as Odd"
    )
    assert import_link("import Base") == Link("import ", "Base", "/files/Base/base.bend", "")


def test_other_lines_have_no_link() -> None:
    assert import_link("import ../up.bend as Up") is None
    assert import_link("def f() -> Nat:") is None


def test_a_declaration_links_its_name_to_the_definition() -> None:
    declared = [Declared("kit@1.0.0.0/main:map.go", "map.go", 2)]
    text = "# Maps.\ndef map.go(xs: List<Nat>) -> Nat:\n  map.go(xs)"
    file = SourceFile("kit@1.0.0.0", HASH, "main.bend", text, {})
    highlights = [
        [mark(0, 7, "comment")],
        [mark(0, 3, "keyword"), mark(4, 10, "function", declaration=True)],
        [mark(2, 8, "function")],
    ]
    assert file_lines(file, declared, highlights) == [
        SourceLine([Run([Piece("# Maps.", "comment")])]),
        SourceLine(
            [
                Run([Piece("def", "keyword"), Piece(" ", None)]),
                Run([Piece("map.go", "function declared")], "/definitions/kit@1.0.0.0/main:map.go"),
                Run([Piece("(xs: List<Nat>) -> Nat:", None)]),
            ]
        ),
        SourceLine([Run([Piece("  ", None), Piece("map.go", "function"), Piece("(xs)", None)])]),
    ]


def test_a_file_without_highlights_is_plain() -> None:
    file = SourceFile("kit@1.0.0.0", HASH, "main.bend", "# A\n# B\n", {})
    assert file_lines(file, [], None) == [
        SourceLine([Run([Piece("# A", None)])]),
        SourceLine([Run([Piece("# B", None)])]),
    ]


def test_variables_operators_and_used_namespaces_have_no_style() -> None:
    highlights = [
        mark(0, 3, "keyword"),
        mark(4, 5, "variable", declaration=True),
        mark(6, 7, "operator"),
        mark(8, 9, "namespace"),
    ]
    assert styled("def x + N", highlights) == [Piece("def", "keyword"), Piece(" x + N", None)]


def test_a_declared_namespace_is_declared_without_a_color() -> None:
    highlights = [mark(0, 2, "keyword"), mark(3, 4, "namespace", declaration=True)]
    assert styled("as N", highlights) == [
        Piece("as", "keyword"),
        Piece(" ", None),
        Piece("N", "namespace declared"),
    ]


def test_a_qualified_name_has_the_style_of_its_name() -> None:
    highlights = [
        mark(0, 5, "namespace"),
        mark(6, 11, "type"),
        mark(12, 13, "namespace"),
        mark(14, 18, "function"),
    ]
    assert styled("Bytes.Bytes B.from(x)", highlights) == [
        Piece("Bytes.Bytes", "type"),
        Piece(" ", None),
        Piece("B.from", "function"),
        Piece("(x)", None),
    ]


def test_a_declared_name_has_the_color_of_its_kind() -> None:
    highlights = [
        mark(0, 4, "keyword"),
        mark(5, 8, "type", declaration=True),
        mark(9, 11, "keyword"),
        mark(12, 16, "type"),
        mark(20, 24, "constructor", declaration=True),
    ]
    assert styled("type Nat is Data:\n  Zero{}", highlights) == [
        Piece("type", "keyword"),
        Piece(" ", None),
        Piece("Nat", "type declared"),
        Piece(" ", None),
        Piece("is", "keyword"),
        Piece(" ", None),
        Piece("Data", "type"),
        Piece(":\n  ", None),
        Piece("Zero", "constructor declared"),
        Piece("{}", None),
    ]


def test_literals_names_and_comments_have_their_kind_as_style() -> None:
    highlights = [
        mark(0, 3, "type"),
        mark(4, 8, "constructor", declaration=True),
        mark(9, 10, "number"),
        mark(11, 14, "string"),
        mark(15, 16, "function"),
        mark(17, 20, "comment"),
    ]
    assert styled('Nat Zero 1 "s" f # z', highlights) == [
        Piece("Nat", "type"),
        Piece(" ", None),
        Piece("Zero", "constructor declared"),
        Piece(" ", None),
        Piece("1", "number"),
        Piece(" ", None),
        Piece('"s"', "string"),
        Piece(" ", None),
        Piece("f", "function"),
        Piece(" ", None),
        Piece("# z", "comment"),
    ]


def test_a_dotted_name_has_one_style() -> None:
    highlights = [
        mark(4, 9, "function", declaration=True),
        mark(10, 15, "function", declaration=True),
    ]
    assert styled("def parse.bytes(b)", highlights) == [
        Piece("def ", None),
        Piece("parse.bytes", "function declared"),
        Piece("(b)", None),
    ]


def import_marks(path: int, alias: int, end: int) -> list[Highlight]:
    return [
        mark(0, 6, "keyword"),
        mark(7, path, "string"),
        mark(path + 1, path + 3, "keyword"),
        mark(path + 4, alias, "namespace", declaration=True),
        mark(alias + 1, end, "comment"),
    ]


def imported(alias: str, comment: str) -> list[Piece]:
    return [
        Piece(" ", None),
        Piece("as", "keyword"),
        Piece(" ", None),
        Piece(alias, "namespace declared"),
        Piece(" ", None),
        Piece(comment, "comment"),
    ]


def test_an_import_line_has_the_highlights_of_its_parts() -> None:
    text = "import kit@1.0.0.0/nat.bend as N # naturals\nimport ../up.bend as Up # up"
    file = SourceFile("kit@1.0.0.0", HASH, "main.bend", text, {})
    highlights = [import_marks(27, 32, 43), import_marks(17, 23, 28)]
    assert file_lines(file, [], highlights) == [
        SourceLine(
            [
                Run([Piece("import", "keyword"), Piece(" ", None)]),
                Run([Piece("kit@1.0.0.0/nat.bend", "string")], "/files/kit@1.0.0.0/nat.bend"),
                Run(imported("N", "# naturals")),
            ]
        ),
        SourceLine(
            [
                Run(
                    [
                        Piece("import", "keyword"),
                        Piece(" ", None),
                        Piece("../up.bend", "string"),
                        *imported("Up", "# up"),
                    ]
                )
            ]
        ),
    ]


def test_a_definition_shows_its_import_lines_and_its_code() -> None:
    detail = DefinitionDetail(
        "kit@1.0.0.0/main:f",
        HASH,
        "kit/main:f",
        "def",
        "f",
        "kit",
        "1.0.0.0",
        "main",
        "# F.\ndef f() -> Nat:",
        [ImportLine("import ./n.bend as N # n", "import kit@1.0.0.0/n.bend as N # n", 1)],
        4,
        6,
        [],
    )
    highlights = {
        1: import_marks(15, 20, 24),
        4: [mark(0, 4, "comment")],
        5: [mark(0, 3, "keyword"), mark(4, 5, "function", declaration=True)],
    }
    assert import_lines(detail, highlights) == [
        SourceLine(
            [
                Run([Piece("import", "keyword"), Piece(" ", None)]),
                Run([Piece("kit@1.0.0.0/n.bend", "string")], "/files/kit@1.0.0.0/n.bend"),
                Run(imported("N", "# n")),
            ]
        )
    ]
    assert body_lines(detail, highlights) == [
        SourceLine([Run([Piece("# F.", "comment")])]),
        SourceLine(
            [
                Run(
                    [
                        Piece("def", "keyword"),
                        Piece(" ", None),
                        Piece("f", "function declared"),
                        Piece("() -> Nat:", None),
                    ]
                )
            ]
        ),
    ]


def test_a_shown_line_that_differs_without_a_link_is_plain() -> None:
    line = source_line("import x", [mark(0, 6, "comment")], "import y")
    assert line == SourceLine([Run([Piece("import y", None)])])


def test_a_long_head_is_cut_after_its_highlights() -> None:
    head = "\n".join(f"  C{index}{{}}" for index in range(30))
    found = [[mark(2, 4, "constructor", declaration=True)] for _ in range(12)]
    lines = head_lines(head, found)
    assert len(lines) == 12
    assert lines[0] == SourceLine(
        [Run([Piece("  ", None), Piece("C0", "constructor declared"), Piece("{}", None)])]
    )
    assert lines[-1] == SourceLine([Run([Piece("  …", None)])])
    assert head_lines("def f() -> Nat:", None) == [
        SourceLine([Run([Piece("def f() -> Nat:", None)])])
    ]


def test_a_reference_splits_the_place_from_the_name() -> None:
    found = reference("kit@1.0.0.0/deep/odd:a.b")
    assert (found.file, found.where, found.name) == (
        "kit@1.0.0.0/deep/odd.bend",
        "kit@1.0.0.0/deep/odd",
        "a.b",
    )
    assert reference("List.map").where == "Base"


def test_a_range_of_lines_is_a_parameter_and_its_first_line() -> None:
    assert lines_href("kit@1.0.0.0/main.bend", 3, 3) == "/files/kit@1.0.0.0/main.bend#L3"
    assert lines_href("kit@1.0.0.0/main.bend", 3, 9) == "/files/kit@1.0.0.0/main.bend?lines=3-9#L3"


def test_a_long_head_is_shortened() -> None:
    head = "\n".join(f"  C{index}{{}}" for index in range(30))
    assert shortened(head).split("\n")[-1] == "  …"
    assert len(shortened(head).split("\n")) == 12
    assert shortened("def f() -> Nat:") == "def f() -> Nat:"
