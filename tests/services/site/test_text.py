from datetime import UTC, datetime

from jendhub.indexing.failures import Place, Problem
from jendhub.indexing.status import GenerationStatus, InvalidRelease, JobCounts
from jendhub.search.engine import Hit
from jendhub.search.packages import (
    DefinitionSummary,
    PackageDetail,
    ReleaseDetail,
)
from jendhub.search.sources import usage
from jendhub.services.site.files import Lines, Matches
from jendhub.services.site.text import (
    ToolLinks,
    UrlLinks,
    render_lines,
    render_matches,
    render_package,
    render_paths,
    render_results,
    render_status,
)
from jendhub.services.site.views import (
    PackageView,
    Result,
    Results,
    StatusView,
)

ORIGIN = "https://jend.test"
LINKS = UrlLinks(ORIGIN)
KIT_MAP = f"{ORIGIN}/read.txt?file_path=kit@1.0.0.0/main.bend&offset=5&limit=2"
PROBLEM = Problem(
    "a fresh name (duplicate declaration: Nat)",
    "'Nat'",
    [Place("kit@2.0.0.0/nat.bend", 3)],
    ["main.bend", "nat.bend"],
)
PROBLEM_TEXT = (
    "   expected a fresh name (duplicate declaration: Nat)\n"
    f"   At {ORIGIN}/read.txt?file_path=kit@2.0.0.0/nat.bend&offset=3.\n"
    "   Files that do not load: main.bend, nat.bend.\n"
)


def result(id_: str, package: str | None) -> Result:
    version = None if package is None else "1.0.0.0"
    found = Hit(
        id_,
        "def",
        "map" if package else id_,
        package,
        version,
        "main",
        "# Maps.\ndef map(xs):",
        4,
        6,
        1.0,
    )
    return Result(found, usage(found.name, package, version, "main"))


def test_results_show_how_to_use_and_read_each_definition() -> None:
    found = Results("map", [result("List.map", None), result("kit@1.0.0.0/main:map", "kit")], 2, 0)
    assert render_results(found, LINKS) == (
        f"1. List.map ({ORIGIN}/read.txt?file_path=Base/base.bend&offset=5&limit=2)\n"
        "   import Base\n   # Maps.\n   def map(xs):\n\n"
        f"2. Kit.map ({KIT_MAP})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Maps.\n   def map(xs):\n"
    )


def test_tool_results_give_read_calls() -> None:
    found = Results("map", [result("kit@1.0.0.0/main:map", "kit")], 3, 0)
    links = ToolLinks("search", {"query": "map", "limit": 1})
    assert render_results(found, links).splitlines() == [
        '1. Kit.map (read(file_path="kit@1.0.0.0/main.bend", offset=5, limit=2))',
        "   import kit@1.0.0.0/main.bend as Kit",
        "   # Maps.",
        "   def map(xs):",
        "",
        'More: search(query="map", limit=1, offset=1)',
    ]


def test_no_results_say_so() -> None:
    assert render_results(Results("x", [], 0, 0), LINKS) == "No results.\n"


def test_lines_are_numbered_and_cut_at_2000_characters() -> None:
    found = Lines("kit@1.0.0.0/main.bend", 3, ["def f:", "x" * 2001], 9)
    assert render_lines(found) == f"3\tdef f:\n4\t{'x' * 2000}\n"
    assert render_lines(Lines("kit@1.0.0.0/e.bend", 1, [], 0)) == "kit@1.0.0.0/e.bend is empty.\n"


def test_grep_files_give_the_count() -> None:
    found = Matches("files_with_matches", ["kit@1.0.0.0/a.bend"], 2)
    assert render_matches(found) == "Found 2 files\nkit@1.0.0.0/a.bend\n"
    assert render_matches(Matches("files_with_matches", [], 0)) == "No files found\n"
    assert render_matches(Matches("content", [], 0)) == "No matches found\n"


def test_glob_gives_one_path_on_each_line() -> None:
    assert render_paths(["kit@1.0.0.0/a.bend"]) == "kit@1.0.0.0/a.bend\n"
    assert render_paths([]) == "No files found\n"


def test_a_package_shows_its_releases_and_a_page_of_definitions() -> None:
    package = PackageDetail(
        "kit",
        [
            ReleaseDetail("2.0.0.0", "0x2", "invalid", [PROBLEM]),
            ReleaseDetail("1.0.0.0", "0x1", "indexed", []),
        ],
        [DefinitionSummary("kit@1.0.0.0/main:map", "def", "map", "main", "def map:", 4, 6)],
        3,
    )
    files = f"{ORIGIN}/glob.txt?pattern=**&path=kit"
    assert render_package(PackageView(package, 2), LINKS) == (
        "kit\n\n"
        f"Releases (2):\n  2.0.0.0  0x2  invalid  {files}@2.0.0.0\n{PROBLEM_TEXT}"
        f"  1.0.0.0  0x1  indexed  {files}@1.0.0.0\n\n"
        f"Definitions (3, 3 to 3 shown):\n  Kit.map ({KIT_MAP})\n   def map:\n"
    )


def test_the_status_shows_each_generation_and_the_invalid_releases() -> None:
    generation = GenerationStatus(
        3,
        "live",
        "live",
        "0.1.2",
        "d2048-binary",
        2,
        datetime(2026, 10, 16, tzinfo=UTC),
        107,
        56650,
        JobCounts(queued=0, running=0, done=107, superseded=2, invalid=10, failed=0),
    )
    invalid = InvalidRelease(
        "kit", "2.0.0.0", "0x9", "0.1.2", [PROBLEM], datetime(2026, 10, 15, tzinfo=UTC)
    )
    assert render_status(StatusView([generation], [invalid], 1, 0), LINKS) == (
        "Generation 3 (live): Base 0.1.2, vectors d2048-binary, indexer 2, 107 releases,"
        " 56650 definitions\n"
        "  Jobs: 0 queued, 0 running, 107 indexed, 2 superseded, 0 failed\n\n"
        f"Invalid releases (1):\n  kit@2.0.0.0 (0x9), Base 0.1.2, 2026-10-15\n{PROBLEM_TEXT}"
    )
