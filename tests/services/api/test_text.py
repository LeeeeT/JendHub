from datetime import UTC, datetime

from jendhub.services.api.text import (
    render_definition,
    render_package,
    render_results,
    render_status,
)
from jendhub.services.contracts import (
    DefinitionPage,
    DefinitionSummary,
    GenerationSummary,
    InvalidReleaseSummary,
    JobCounts,
    PackagePage,
    ReleaseSummary,
    SearchHit,
    SearchResults,
    StatusPage,
)


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


def test_a_package_shows_its_releases_and_a_page_of_definitions() -> None:
    page = PackagePage(
        name="kit",
        releases=[
            ReleaseSummary(version="2.0.0.0", hash="0x2", state="invalid", reason="no load"),
            ReleaseSummary(version="1.0.0.0", hash="0x1", state="indexed", reason=None),
        ],
        definitions=[
            DefinitionSummary(
                id="kit@1.0.0.0/main:map", kind="def", name="map", module="main", head="def map:"
            )
        ],
        definitions_total=3,
        definitions_offset=2,
    )
    assert render_package(page) == (
        "kit\n\n"
        "Releases (2):\n  2.0.0.0  0x2  invalid\n   no load\n  1.0.0.0  0x1  indexed\n\n"
        "Definitions (3, 3 to 3 shown):\n  kit@1.0.0.0/main:map\n   def map:\n"
    )


def test_the_status_shows_each_generation_and_the_invalid_releases() -> None:
    page = StatusPage(
        generations=[
            GenerationSummary(
                id=3,
                state="live",
                base_tag="0.1.2",
                indexer_version=2,
                created_at=datetime(2026, 10, 16, tzinfo=UTC),
                releases=107,
                definitions=56650,
                jobs=JobCounts(queued=0, running=0, done=107, superseded=2, invalid=10, failed=0),
            )
        ],
        invalid_releases=[
            InvalidReleaseSummary(
                name=None,
                version=None,
                hash="0x9",
                base_tag="0.1.2",
                reason="no load",
                found_at=datetime(2026, 10, 15, tzinfo=UTC),
            )
        ],
        invalid_releases_total=1,
        invalid_releases_offset=0,
    )
    assert render_status(page) == (
        "Generation 3 (live): Base 0.1.2, indexer 2, 107 releases, 56650 definitions\n"
        "  Jobs: 0 queued, 0 running, 107 done, 2 superseded, 10 invalid, 0 failed\n\n"
        "Invalid releases (1):\n  0x9 (0x9), Base 0.1.2, 2026-10-15\n   no load\n"
    )


def test_a_status_with_no_generation_says_so() -> None:
    page = StatusPage(
        generations=[], invalid_releases=[], invalid_releases_total=0, invalid_releases_offset=0
    )
    assert render_status(page) == "No generation.\n\nInvalid releases (0):\n"
