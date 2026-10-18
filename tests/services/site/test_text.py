from datetime import UTC, datetime

from jendhub.indexing.failures import Place, Problem
from jendhub.indexing.status import GenerationStatus, InvalidRelease, JobCounts
from jendhub.search.definitions import DefinitionDetail
from jendhub.search.engine import Hit
from jendhub.search.packages import (
    DefinitionSummary,
    PackageDetail,
    ReleaseDetail,
)
from jendhub.search.sources import usage
from jendhub.services.site.text import (
    UrlLinks,
    render_definition,
    render_package,
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
KIT_MAP = f"{ORIGIN}/src/kit@1.0.0.0/main.bend?def=map"
PROBLEM = Problem(
    "a fresh name (duplicate declaration: Nat)",
    "'Nat'",
    [Place("kit@2.0.0.0/nat.bend", 3)],
    ["main.bend", "nat.bend"],
)
PROBLEM_TEXT = (
    "   expected a fresh name (duplicate declaration: Nat)\n"
    f"   At {ORIGIN}/src/kit@2.0.0.0/nat.bend line 3.\n"
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
        1.0,
    )
    return Result(found, usage(found.name, package, version, "main"))


def test_results_show_how_to_use_each_definition() -> None:
    found = Results("map", [result("List.map", None), result("kit@1.0.0.0/main:map", "kit")], 2, 0)
    assert render_results(found, LINKS) == (
        f"1. List.map ({ORIGIN}/src/Base/base.bend?def=List.map)\n"
        "   import Base\n   # Maps.\n   def map(xs):\n\n"
        f"2. Kit.map ({KIT_MAP})\n"
        "   import kit@1.0.0.0/main.bend as Kit\n   # Maps.\n   def map(xs):\n"
    )


def test_no_results_say_so() -> None:
    assert render_results(Results("x", [], 0, 0), LINKS) == "No results.\n"


def test_a_definition_shows_how_to_use_it_and_its_uses() -> None:
    detail = DefinitionDetail(
        "kit@1.0.0.0/main:map",
        "def",
        "map",
        "kit",
        "1.0.0.0",
        "main",
        "def map(xs):\n  xs",
        ["import Base"],
        4,
        6,
        ["List", "kit@1.0.0.0/deep/odd:a?b"],
    )
    view = DefinitionView(detail, usage("map", "kit", "1.0.0.0", "main"))
    assert render_definition(view, LINKS) == (
        "Kit.map (def, kit@1.0.0.0, lines 5-6)\n"
        "Use: import kit@1.0.0.0/main.bend as Kit, then call Kit.map.\n"
        f"File: {ORIGIN}/src/kit@1.0.0.0/main.bend\n\n"
        "import Base\n\ndef map(xs):\n  xs\n\n"
        "Uses (2):\n"
        f"  {ORIGIN}/src/Base/base.bend?def=List\n"
        f"  {ORIGIN}/src/kit@1.0.0.0/deep/odd.bend?def=a%3Fb\n"
    )


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
    assert render_package(PackageView(package, 2), LINKS) == (
        "kit\n\n"
        f"Releases (2):\n  2.0.0.0  0x2  invalid  {ORIGIN}/src/kit@2.0.0.0/\n{PROBLEM_TEXT}"
        f"  1.0.0.0  0x1  indexed  {ORIGIN}/src/kit@1.0.0.0/\n\n"
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
        "  Jobs: 0 queued, 0 running, 107 indexed, 2 superseded, 10 invalid, 0 failed\n\n"
        f"Invalid releases (1):\n  kit@2.0.0.0 (0x9), Base 0.1.2, 2026-10-15\n{PROBLEM_TEXT}"
    )
