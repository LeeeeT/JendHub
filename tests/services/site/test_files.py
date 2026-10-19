from pathlib import Path

import pytest
from fastapi import HTTPException

from jendhub.services.site import files
from jendhub.services.site.arguments import FilesArguments, GrepArguments
from jendhub.services.site.files import Page, Place, grep_files, list_files, read_lines

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


async def grep(place: Place, **arguments: object) -> Page:
    return await grep_files(place, GrepArguments.model_validate({"path": LABEL, **arguments}))


async def files_of(place: Place, **arguments: object) -> Page:
    return await list_files(place, FilesArguments.model_validate({"path": LABEL, **arguments}))


async def test_read_gives_the_lines_from_start_to_end(release: Place) -> None:
    found = await read_lines(inside(release, "main.bend"), 3, 4)
    assert (found.path, found.first, found.lines, found.total, found.cut) == (
        f"{LABEL}/main.bend",
        3,
        ["def f() -> Nat:", "  U.g()"],
        4,
        False,
    )
    assert (await read_lines(inside(release, "main.bend"), 2, 99)).lines == [
        "",
        "def f() -> Nat:",
        "  U.g()",
    ]


async def test_read_cuts_a_long_range(release: Place, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "MAX_LINES", 2)
    found = await read_lines(inside(release, "main.bend"), 1, None)
    assert (found.lines, found.cut, found.last) == (["import ./src/util.bend as U", ""], True, 2)
    assert not (await read_lines(inside(release, "main.bend"), 3, None)).cut


@pytest.mark.parametrize(
    ("path", "start", "end", "status"),
    [
        ("main.bend", 5, None, 400),
        ("main.bend", 3, 2, 400),
        ("src", 1, None, 400),
        ("no.bend", 1, None, 404),
    ],
)
async def test_read_refuses_what_it_cannot_read(
    release: Place, path: str, start: int, end: int | None, status: int
) -> None:
    with pytest.raises(HTTPException) as raised:
        await read_lines(inside(release, path), start, end)
    assert raised.value.status_code == status


async def test_grep_gives_lines_with_the_numbers_that_read_takes(release: Place) -> None:
    assert (await grep(release, pattern="def")).entries == [
        f"{LABEL}/.hidden.bend:1:def h() -> Nat:",
        f"{LABEL}/main.bend:3:def f() -> Nat:",
        f"{LABEL}/src/util.bend:1:def g() -> Nat:",
    ]
    inner = await grep(inside(release, "src"), pattern="def", mode="files")
    assert inner.entries == [f"{LABEL}/src/util.bend"]


async def test_grep_pages_through_the_results(release: Place) -> None:
    first = await grep(release, pattern="def", limit=2)
    assert (len(first.entries), first.more) == (2, True)
    last = await grep(release, pattern="def", limit=2, offset=2)
    assert (last.entries, last.offset, last.more) == (
        [f"{LABEL}/src/util.bend:1:def g() -> Nat:"],
        2,
        False,
    )


async def test_grep_follows_its_options(release: Place) -> None:
    assert (
        await grep(release, pattern="DEF G", ignore_case=True, glob="src/**", mode="files")
    ).entries == [f"{LABEL}/src/util.bend"]
    assert (await grep(release, pattern="def", glob="!src/**", mode="files")).entries == [
        f"{LABEL}/.hidden.bend",
        f"{LABEL}/main.bend",
    ]
    assert (await grep(release, pattern="e", mode="counts")).entries == [
        f"{LABEL}/.hidden.bend:1",
        f"{LABEL}/main.bend:2",
        f"{LABEL}/src/util.bend:1",
    ]
    around = await grep(release, pattern="U.g", before=1)
    assert around.entries == [
        f"{LABEL}/main.bend-3-def f() -> Nat:",
        f"{LABEL}/main.bend:4:  U.g()",
    ]
    lines = await grep(release, pattern=r"Nat:\n  1n", multiline=True)
    assert lines.entries == [
        f"{LABEL}/src/util.bend:1:def g() -> Nat:",
        f"{LABEL}/src/util.bend:2:  1n",
    ]


async def test_grep_explains_an_invalid_pattern(release: Place) -> None:
    with pytest.raises(HTTPException) as raised:
        await grep(release, pattern="Box{value")
    assert raised.value.status_code == 400
    assert str(raised.value.detail).startswith("Invalid argument: regex parse error")


async def test_files_gives_the_paths_in_pages(release: Place) -> None:
    every = await files_of(release, limit=2)
    assert (every.entries, every.more) == ([f"{LABEL}/.hidden.bend", f"{LABEL}/main.bend"], True)
    assert (await files_of(inside(release, "src"))).entries == [f"{LABEL}/src/util.bend"]
    with pytest.raises(HTTPException):
        await files_of(inside(release, "main.bend"))


@pytest.mark.parametrize(
    ("glob", "paths"),
    [
        ("*.bend", [".hidden.bend", "main.bend", "src/util.bend"]),
        ("util.bend", ["src/util.bend"]),
        ("src/**", ["src/util.bend"]),
        ("/main.bend", ["main.bend"]),
        ("!src/**", [".hidden.bend", "main.bend"]),
    ],
)
async def test_files_and_grep_match_a_glob_in_the_same_way(
    release: Place, glob: str, paths: list[str]
) -> None:
    expected = [f"{LABEL}/{path}" for path in paths]
    assert (await files_of(release, glob=glob)).entries == expected
    assert (await grep(release, pattern="def", glob=glob, mode="files")).entries == expected
    assert (await files_of(inside(release, "src"), glob=glob)).entries == [
        path for path in expected if path.startswith(f"{LABEL}/src/")
    ]
