from datetime import UTC, datetime

from jendhub.indexing.failures import Place, Problem
from jendhub.indexing.status import GenerationStatus, InvalidRelease, JobCounts
from jendhub.search.definitions import DefinitionDetail, ImportLine
from jendhub.search.engine import Hit
from jendhub.search.packages import (
    DefinitionSummary,
    PackageDetail,
    ReleaseDetail,
)
from jendhub.search.sources import usage
from jendhub.services.site.files import Lines, Page
from jendhub.services.site.text import (
    ToolLinks,
    UrlLinks,
    render_definition,
    render_lines,
    render_matches,
    render_package,
    render_paths,
    render_results,
    render_status,
)
from jendhub.services.site.views import (
    DefinitionView,
    PackageView,
    Result,
    Results,
    StatusView,
)

ORIGIN = "https://jend.test"
LINKS = UrlLinks(ORIGIN)
KIT_MAP = f"{ORIGIN}/definition.txt?path=kit@1.0.0.0/main.bend&name=map"
PROBLEM = Problem(
    "a fresh name (duplicate declaration: Nat)",
    "'Nat'",
    [Place("kit@2.0.0.0/nat.bend", 3)],
    ["main.bend", "nat.bend"],
)
PROBLEM_TEXT = (
    "   expected a fresh name (duplicate declaration: Nat)\n"
    f"   At {ORIGIN}/read.txt?path=kit@2.0.0.0/nat.bend&start_line=3.\n"
    "   Files that do not load: main.bend, nat.bend.\n"
)


def result(id_: str, package: str | None) -> Result:
    version = None if package is None else "1.0.0.0"
    found = Hit(
        id_,
        f"{package}/main:map" if package else id_,
        "def",
        "map" if package else id_,
        package,
        version,
        "main",
        "# Maps.\ndef map(xs):",
        1.0,
    )
    return Result(found, usage(found.name, package, version, "main"), 0)


def test_results_show_how_to_use_and_open_each_definition() -> None:
    found = Results("map", [result("List.map", None), result("kit@1.0.0.0/main:map", "kit")], 2, 0)
    assert render_results(found, LINKS) == (
        f"1. List.map ({ORIGIN}/definition.txt?path=Base/base.bend&name=List.map)\n"
        "   import Base\n   # Maps.\n   def map(xs):\n\n"
        f"2. Kit.map ({KIT_MAP})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Maps.\n   def map(xs):\n"
    )


def test_tool_results_give_definition_calls() -> None:
    found = Results("map", [result("kit@1.0.0.0/main:map", "kit")], 3, 0)
    links = ToolLinks("search", {"query": "map", "limit": 1})
    assert render_results(found, links).splitlines() == [
        '1. Kit.map (definition(path="kit@1.0.0.0/main.bend", name="map"))',
        "   import kit@1.0.0.0/main.bend as Kit",
        "   # Maps.",
        "   def map(xs):",
        "",
        'More: search(query="map", limit=1, offset=1)',
    ]


def test_no_results_say_so() -> None:
    assert render_results(Results("x", [], 0, 0), LINKS) == "No results.\n"


def test_a_definition_gives_its_code_imports_lines_and_uses() -> None:
    detail = DefinitionDetail(
        "kit@1.0.0.0/main:map",
        "0x" + "a" * 32,
        "kit/main:map",
        "def",
        "map",
        "kit",
        "1.0.0.0",
        "main",
        "def map(xs):\n  xs",
        [ImportLine("import Base", "import Base", 0)],
        4,
        6,
        ["List", "kit@1.0.0.0/deep/odd:a?b"],
    )
    view = DefinitionView(detail, usage("map", "kit", "1.0.0.0", "main"))
    assert render_definition(view, ToolLinks("definition")) == (
        "Kit.map: def of kit@1.0.0.0\n"
        "Import: import kit@1.0.0.0/main.bend as Kit\n"
        'Lines 5-6 of kit@1.0.0.0/main.bend: read(path="kit@1.0.0.0/main.bend", start_line=5,'
        " end_line=6)\n\n"
        "import Base\n\ndef map(xs):\n  xs\n\n"
        "Uses (2):\n"
        '  definition(path="Base/base.bend", name="List")\n'
        '  definition(path="kit@1.0.0.0/deep/odd.bend", name="a?b")\n'
    )


def test_lines_are_numbered_and_a_cut_answer_gives_the_next_call() -> None:
    links = ToolLinks("read", {"path": "kit@1.0.0.0/main.bend"})
    found = Lines("kit@1.0.0.0/main.bend", 3, ["def f:", "x" * 2001], 9, cut=True)
    assert render_lines(found, links) == (
        f"3\tdef f:\n4\t{'x' * 2000}\n"
        'Lines 3-4 of 9. More: read(path="kit@1.0.0.0/main.bend", start_line=5)\n'
    )
    complete = Lines("kit@1.0.0.0/main.bend", 3, ["a", "b"], 9, cut=False)
    assert render_lines(complete, links) == "3\ta\n4\tb\n"
    empty = Lines("kit@1.0.0.0/e.bend", 1, [], 0, cut=False)
    assert render_lines(empty, links) == "kit@1.0.0.0/e.bend is empty.\n"


def test_a_page_of_results_gives_the_next_call() -> None:
    links = UrlLinks(ORIGIN, "/grep.txt", (("pattern", "f"), ("path", "kit@1.0.0.0")))
    found = Page(["kit@1.0.0.0/a.bend:1:def f:"], 0, more=True)
    assert render_matches(found, links) == (
        "kit@1.0.0.0/a.bend:1:def f:\n"
        f"More: {ORIGIN}/grep.txt?pattern=f&path=kit@1.0.0.0&offset=1\n"
    )
    assert render_matches(Page([], 0, more=False), links) == "No matches.\n"
    assert (
        render_paths(Page(["kit@1.0.0.0/a.bend"], 0, more=False), links) == "kit@1.0.0.0/a.bend\n"
    )
    assert render_paths(Page([], 0, more=False), links) == "No files match.\n"


def test_a_package_shows_its_releases_and_a_page_of_definitions() -> None:
    package = PackageDetail(
        "kit",
        [
            ReleaseDetail("2.0.0.0", "0x2", "invalid", [PROBLEM]),
            ReleaseDetail("1.0.0.0", "0x1", "indexed", []),
        ],
        [DefinitionSummary("kit@1.0.0.0/main:map", "def", "map", "main", "def map:")],
        3,
    )
    files = f"{ORIGIN}/files.txt?path=kit"
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
