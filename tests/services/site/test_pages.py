from jendhub.services.site.pages import Link, source_line


def test_an_import_line_links_to_its_file() -> None:
    assert source_line("import kit@1.0.0.0/deep/odd.bend as Odd").link == Link(
        "import ", "kit@1.0.0.0/deep/odd.bend", "kit@1.0.0.0/deep/odd.bend", " as Odd"
    )
    assert source_line("import Base").link == Link("import ", "Base", "Base/base.bend", "")


def test_other_lines_have_no_link() -> None:
    assert source_line("import ../up.bend as Up").link is None
    assert source_line("def f() -> Nat:").link is None
