from datetime import UTC, datetime

from jendhub.services.api.text import (
    Links,
    render_definition,
    render_files,
    render_package,
    render_results,
    render_status,
)
from jendhub.services.contracts import (
    DefinitionPage,
    DefinitionSummary,
    FileList,
    GenerationSummary,
    InvalidReleaseSummary,
    JobCounts,
    PackagePage,
    ReleaseSummary,
    SearchHit,
    SearchResults,
    StatusPage,
)

ORIGIN = "https://jendhub.test"
LINKS = Links(ORIGIN)


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
        import_line="import Base" if package is None else "import kit@1.0.0.0/main.bend as Kit",
        call_name="List.map" if package is None else "Kit.map",
        file="Base/base.bend" if package is None else "kit@1.0.0.0/main.bend",
    )


def test_results_show_how_to_use_each_definition() -> None:
    found = SearchResults(
        results=[hit("List.map", None), hit("kit@1.0.0.0/main:map", "kit")], total=2, offset=0
    )
    assert render_results(found, LINKS) == (
        "1. List.map (Base)\n   import Base\n   # Maps.\n   def map(xs):\n"
        f"   source: {ORIGIN}/definitions/List.map\n\n"
        "2. Kit.map (kit@1.0.0.0/main:map)\n   import kit@1.0.0.0/main.bend as Kit\n"
        f"   # Maps.\n   def map(xs):\n   source: {ORIGIN}/definitions/kit@1.0.0.0/main:map\n"
    )


def test_results_link_to_the_next_results() -> None:
    found = SearchResults(results=[hit("List.map", None)], total=5, offset=2)
    links = Links(ORIGIN, "/search", {"q": "map", "limit": "1", "offset": "2"})
    text = render_results(found, links)
    assert text.startswith("3. List.map (Base)\n")
    assert text.endswith(f"\nMore: {ORIGIN}/search?q=map&limit=1&offset=3\n")


def test_no_results_say_so() -> None:
    assert render_results(SearchResults(results=[], total=0, offset=0), LINKS) == "No results.\n"


def test_a_definition_shows_where_it_is_and_a_page_of_users() -> None:
    page = DefinitionPage(
        id="kit@1.0.0.0/main:map",
        kind="def",
        name="map",
        package="kit",
        version="1.0.0.0",
        module="main",
        text="def map(xs):\n  xs",
        imports=["import Base"],
        import_line="import kit@1.0.0.0/main.bend as Kit",
        call_name="Kit.map",
        file="kit@1.0.0.0/main.bend",
        first_line=4,
        end_line=6,
        uses=["List"],
        used_by=["b", "c"],
        used_by_total=5,
        used_by_offset=1,
    )
    links = Links(ORIGIN, "/definitions/kit@1.0.0.0/main:map", {"used_by_offset": "1"})
    assert render_definition(page, links) == (
        "Kit.map (kit@1.0.0.0/main:map) (def, kit@1.0.0.0, lines 5-6)\n"
        "Use: import kit@1.0.0.0/main.bend as Kit, then call Kit.map.\n"
        f"File: {ORIGIN}/files/kit@1.0.0.0/main.bend\n\n"
        "import Base\n\ndef map(xs):\n  xs\n\n"
        "Uses (1):\n  List\n\n"
        "Used by (5, 2 to 3 shown):\n  b\n  c\n"
        f"More: {ORIGIN}/definitions/kit@1.0.0.0/main:map?used_by_offset=3\n\n"
        f"Each ID is at {ORIGIN}/definitions/<id>\n"
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
    assert render_package(page, LINKS) == (
        "kit\n\n"
        f"Releases (2):\n  2.0.0.0  0x2  invalid  {ORIGIN}/files/kit@2.0.0.0\n   no load\n"
        f"  1.0.0.0  0x1  indexed  {ORIGIN}/files/kit@1.0.0.0\n\n"
        "Definitions (3, 3 to 3 shown):\n  kit@1.0.0.0/main:map\n   def map:\n\n"
        f"Each ID is at {ORIGIN}/definitions/<id>\n"
    )


def test_the_status_shows_each_generation_and_the_invalid_releases() -> None:
    page = StatusPage(
        generations=[
            GenerationSummary(
                id=3,
                state="live",
                base_tag="0.1.2",
                vectors="d2048-binary",
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
    assert render_status(page, LINKS) == (
        "Generation 3 (live): Base 0.1.2, vectors d2048-binary, indexer 2, 107 releases,"
        " 56650 definitions\n"
        "  Jobs: 0 queued, 0 running, 107 done, 2 superseded, 10 invalid, 0 failed\n\n"
        "Invalid releases (1):\n  0x9 (0x9), Base 0.1.2, 2026-10-15\n   no load\n"
    )


def test_a_status_with_no_generation_says_so() -> None:
    page = StatusPage(
        generations=[], invalid_releases=[], invalid_releases_total=0, invalid_releases_offset=0
    )
    assert render_status(page, LINKS) == "No generation.\n\nInvalid releases (0):\n"


def test_the_files_of_a_release_are_full_urls() -> None:
    page = FileList(release="kit@1.0.0.0", hash="0x1", paths=["main.bend", "src/a.bend"])
    assert render_files(page, LINKS) == (
        "kit@1.0.0.0\n\n"
        f"  {ORIGIN}/files/kit@1.0.0.0/main.bend\n  {ORIGIN}/files/kit@1.0.0.0/src/a.bend\n"
    )
