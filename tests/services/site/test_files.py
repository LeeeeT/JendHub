from pathlib import Path

import pytest
from fastapi import HTTPException

from jendhub.services.site.arguments import GrepArguments
from jendhub.services.site.files import Matches, Place, glob_files, grep_files, read_lines

LABEL = "kit@1.0.0.0"
FILES = {
    "main.bend": "import ./src/util.bend as U\n\ndef f() -> Nat:\n  U.g()\n",
    "src/util.bend": "def g() -> Nat:\n  1n\n",
    ".hidden.bend": "def h() -> Nat:\n  2n\n",
}


@pytest.fixture
def release(tmp_path: Path) -> Place:
    for path, text in FILES.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    return Place(LABEL, tmp_path, "")


def inside(release: Place, path: str) -> Place:
    return Place(release.label, release.root, path)


async def grep(place: Place, **arguments: object) -> Matches:
    return await grep_files(place, GrepArguments.model_validate({"path": LABEL, **arguments}))


async def test_read_gives_the_asked_lines(release: Place) -> None:
    found = await read_lines(inside(release, "main.bend"), 2, 1)
    assert (found.path, found.first, found.lines, found.total) == (
        f"{LABEL}/main.bend",
        3,
        ["def f() -> Nat:"],
        4,
    )


@pytest.mark.parametrize(
    ("path", "offset", "status"), [("main.bend", 4, 400), ("src", 1, 400), ("no.bend", 1, 404)]
)
async def test_read_refuses_what_it_cannot_read(
    release: Place, path: str, offset: int, status: int
) -> None:
    with pytest.raises(HTTPException) as raised:
        await read_lines(inside(release, path), offset, 10)
    assert raised.value.status_code == status


async def test_grep_gives_the_paths_that_read_takes(release: Place) -> None:
    found = await grep(release, pattern="def")
    assert found.entries == [
        f"{LABEL}/.hidden.bend",
        f"{LABEL}/main.bend",
        f"{LABEL}/src/util.bend",
    ]
    assert found.total == 3
    inner = await grep(inside(release, "src"), pattern="def", output_mode="content")
    assert inner.entries == [f"{LABEL}/src/util.bend:1:def g() -> Nat:"]


async def test_grep_pages_through_the_matching_lines(release: Place) -> None:
    found = await grep(release, pattern="def", output_mode="content", head_limit=1, offset=1)
    assert found.entries == [f"{LABEL}/main.bend:3:def f() -> Nat:"]


async def test_grep_follows_the_options_of_ripgrep(release: Place) -> None:
    assert (await grep(release, pattern="DEF G", **{"-i": True}, glob="src/**")).entries == [
        f"{LABEL}/src/util.bend"
    ]
    assert (await grep(release, pattern="e", output_mode="count")).entries == [
        f"{LABEL}/.hidden.bend:1",
        f"{LABEL}/main.bend:2",
        f"{LABEL}/src/util.bend:1",
    ]
    around = await grep(release, pattern="U.g", output_mode="content", **{"-B": 1})
    assert around.entries == [
        f"{LABEL}/main.bend-3-def f() -> Nat:",
        f"{LABEL}/main.bend:4:  U.g()",
    ]
    lines = await grep(release, pattern=r"Nat:\n  1n", output_mode="content", multiline=True)
    assert lines.entries == [
        f"{LABEL}/src/util.bend:1:def g() -> Nat:",
        f"{LABEL}/src/util.bend:2:  1n",
    ]


async def test_grep_explains_an_invalid_pattern(release: Place) -> None:
    with pytest.raises(HTTPException) as raised:
        await grep(release, pattern="(")
    assert raised.value.status_code == 400
    assert str(raised.value.detail).startswith("Invalid grep: regex parse error")


async def test_glob_gives_the_matching_paths(release: Place) -> None:
    assert await glob_files(release, "*.bend") == [
        f"{LABEL}/.hidden.bend",
        f"{LABEL}/main.bend",
    ]
    assert await glob_files(inside(release, "src"), "**/*.bend") == [f"{LABEL}/src/util.bend"]
    with pytest.raises(HTTPException):
        await glob_files(inside(release, "main.bend"), "*")
