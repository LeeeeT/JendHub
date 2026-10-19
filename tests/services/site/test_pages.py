from jendhub.search.sources import Declared
from jendhub.services.site.pages import (
    Link,
    lines_href,
    reference,
    shortened,
    source_line,
    source_lines,
)


def test_an_import_line_links_to_its_file() -> None:
    assert source_line("import kit@1.0.0.0/deep/odd.bend as Odd").link == Link(
        "import ", "kit@1.0.0.0/deep/odd.bend", "/files/kit@1.0.0.0/deep/odd.bend", " as Odd"
    )
    assert source_line("import Base").link == Link("import ", "Base", "/files/Base/base.bend", "")


def test_other_lines_have_no_link() -> None:
    assert source_line("import ../up.bend as Up").link is None
    assert source_line("def f() -> Nat:").link is None


def test_a_declaration_links_its_name_to_the_definition() -> None:
    declared = [Declared("kit@1.0.0.0/main:map.go", "map.go", 2)]
    lines = source_lines("# Maps.\ndef map.go(xs: List<Nat>) -> Nat:\n  map.go(xs)", declared)
    assert [line.link for line in lines] == [
        None,
        Link("def ", "map.go", "/definitions/kit@1.0.0.0/main:map.go", "(xs: List<Nat>) -> Nat:"),
        None,
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
