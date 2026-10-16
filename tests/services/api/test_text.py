from jendhub.services.api.text import render_definition, render_results
from jendhub.services.contracts import DefinitionPage, SearchHit, SearchResults


def hit(id_: str, package: str | None) -> SearchHit:
    return SearchHit(
        id=id_,
        kind="def",
        name="map",
        package=package,
        version=None if package is None else "1.0.0.0",
        module="main",
        head="# Maps.\ndef map(xs):",
        score=1.0,
    )


def test_results_show_each_id_and_its_head() -> None:
    found = SearchResults(results=[hit("List.map", None), hit("kit@1.0.0.0/main:map", "kit")])
    assert render_results(found) == (
        "1. List.map (Base)\n   # Maps.\n   def map(xs):\n\n"
        "2. kit@1.0.0.0/main:map\n   # Maps.\n   def map(xs):\n"
    )


def test_no_results_say_so() -> None:
    assert render_results(SearchResults(results=[])) == "No results.\n"


def test_a_definition_shows_where_it_is_and_a_page_of_users() -> None:
    page = DefinitionPage(
        id="kit@1.0.0.0/main:map",
        kind="def",
        name="map",
        package="kit",
        version="1.0.0.0",
        module="main",
        text="def map(xs):\n  xs",
        first_line=4,
        end_line=6,
        uses=["List"],
        used_by=["b", "c"],
        used_by_total=5,
        used_by_offset=1,
    )
    assert render_definition(page) == (
        "kit@1.0.0.0/main:map (def, kit@1.0.0.0, main.bend, lines 5-6)\n\n"
        "def map(xs):\n  xs\n\n"
        "Uses (1):\n  List\n\n"
        "Used by (5, 2 to 3 shown):\n  b\n  c\n"
    )
