import asyncio
from asyncio.subprocess import DEVNULL, PIPE, Process
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from jendhub.db.pool import Pool
from jendhub.hub.api import valid_path
from jendhub.hub.library import Library, file_paths
from jendhub.search.sources import BASE, base_tag, read_text, stored_release

MAX_LINES = 2000
MAX_COLUMNS = 2000
MAX_ENTRIES = 1000
MAX_PATHS = 1000
GREP_SECONDS = 10.0
RIPGREP = "rg"
HERE = "./"
NOT_READY = "The search index is not ready. Try again later."
RELEASE_PATH = (
    "One release, or a directory or a file in it, for example `kit@1.0.0.0` or"
    " `kit@1.0.0.0/src`. Base is `Base`."
)

type OutputMode = Literal["content", "files_with_matches", "count"]


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: Annotated[
        str,
        Field(
            min_length=1,
            description="The file to read, as an import line names it, for example"
            " `kit@1.0.0.0/json.bend`. Base is `Base/base.bend`.",
        ),
    ]
    offset: Annotated[int, Field(ge=1, description="The line number to start reading from.")] = 1
    limit: Annotated[int, Field(ge=1, le=MAX_LINES, description="The number of lines to read.")] = (
        MAX_LINES
    )


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Annotated[
        str,
        Field(
            min_length=1,
            description="The regular expression to find in the file contents, in ripgrep syntax.",
        ),
    ]
    path: Annotated[str, Field(min_length=1, description=RELEASE_PATH)]
    glob: Annotated[
        str | None,
        Field(description="A glob that selects the files to search, for example `src/**`."),
    ] = None
    output_mode: Annotated[
        OutputMode,
        Field(
            description="`content` gives the matching lines, `files_with_matches` gives the"
            " paths of the files, and `count` gives the number of matching lines in each file."
        ),
    ] = "files_with_matches"
    before: Annotated[
        int | None,
        Field(alias="-B", ge=0, description="The number of lines to show before each match."),
    ] = None
    after: Annotated[
        int | None,
        Field(alias="-A", ge=0, description="The number of lines to show after each match."),
    ] = None
    around: Annotated[
        int | None,
        Field(
            alias="-C",
            ge=0,
            description="The number of lines to show before and after each match.",
        ),
    ] = None
    context: Annotated[int | None, Field(ge=0, description="The same as -C.")] = None
    line_numbers: Annotated[
        bool, Field(alias="-n", description="Show the number of each line.")
    ] = True
    ignore_case: Annotated[bool, Field(alias="-i", description="Ignore the case of letters.")] = (
        False
    )
    only_matching: Annotated[
        bool, Field(alias="-o", description="Show only the matching part of each line.")
    ] = False
    multiline: Annotated[
        bool,
        Field(description="Let a match go across lines, and let `.` match a line end."),
    ] = False
    head_limit: Annotated[
        int,
        Field(ge=1, le=MAX_ENTRIES, description="The number of lines, paths or counts to give."),
    ] = 250
    offset: Annotated[
        int, Field(ge=0, description="The number of lines, paths or counts to skip.")
    ] = 0


class GlobArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Annotated[
        str,
        Field(
            min_length=1,
            description="The glob that the paths must match, for example `**/*.bend`.",
        ),
    ]
    path: Annotated[str, Field(min_length=1, description=RELEASE_PATH)]
    offset: Annotated[int, Field(ge=0, description="The number of paths to skip.")] = 0


@dataclass(frozen=True)
class Place:
    label: str
    root: Path
    inner: str

    @property
    def path(self) -> str:
        return f"{self.label}/{self.inner}" if self.inner else self.label

    @property
    def target(self) -> Path:
        return self.root / self.inner if self.inner else self.root


@dataclass(frozen=True)
class Lines:
    path: str
    offset: int
    lines: list[str]
    total: int


@dataclass(frozen=True)
class Matches:
    mode: OutputMode
    entries: list[str]
    total: int


async def locate(pool: Pool, library: Library, generation: int | None, path: str) -> Place:
    release, _, inner = path.removesuffix("/").partition("/")
    if inner and not valid_path(inner):
        raise HTTPException(400, f"{path} is not a path in a release.")
    async with pool.connection() as connection:
        if release == BASE:
            tag = None if generation is None else await base_tag(connection, generation)
            if tag is None:
                raise HTTPException(503, NOT_READY)
            return Place(BASE, library.base_directory(tag), inner)
        stored = await stored_release(connection, release)
    if stored is None or stored.hash is None:
        raise HTTPException(404, f"The store has no release {release}.")
    return Place(stored.label, library.package_directory(stored.hash), inner)


def split_lines(text: str) -> list[str]:
    return text.removesuffix("\n").split("\n") if text else []


async def read_lines(place: Place, offset: int, limit: int) -> Lines:
    if place.target.is_dir():
        raise HTTPException(400, f"{place.path} is a directory. Use glob to list its files.")
    text = await asyncio.to_thread(read_text, place.target)
    if text is None:
        raise HTTPException(404, f"The store has no file {place.path}.")
    lines = split_lines(text)
    if lines and offset > len(lines):
        raise HTTPException(400, f"{place.path} has {len(lines)} lines.")
    return Lines(place.path, offset, lines[offset - 1 : offset - 1 + limit], len(lines))


def ripgrep_command(given: GrepArguments, target: str) -> list[str]:
    command = [
        RIPGREP,
        "--no-config",
        "--no-messages",
        "--color=never",
        "--no-heading",
        "--with-filename",
        "--sort=path",
        "--hidden",
        "--no-ignore",
        f"--max-columns={MAX_COLUMNS}",
        "--max-columns-preview",
    ]
    if given.ignore_case:
        command.append("--ignore-case")
    if given.multiline:
        command += ["--multiline", "--multiline-dotall"]
    if given.glob is not None:
        command.append(f"--glob={given.glob}")
    match given.output_mode:
        case "files_with_matches":
            command.append("--files-with-matches")
        case "count":
            command.append("--count")
        case "content":
            command.append("--line-number" if given.line_numbers else "--no-line-number")
            if given.only_matching:
                command.append("--only-matching")
            around = given.context if given.around is None else given.around
            for flag, lines in [
                ("--context", around),
                ("--before-context", given.before),
                ("--after-context", given.after),
            ]:
                if lines is not None:
                    command.append(f"{flag}={lines}")
    return [*command, f"--regexp={given.pattern}", "--", target]


def shown(line: bytes, label: str) -> str:
    text = line.decode(errors="replace").removesuffix("\n")
    return f"{label}/{text.removeprefix(HERE)}" if text.startswith(HERE) else text


async def collect(process: Process, label: str, wanted: int | None) -> tuple[list[str], str]:
    stdout, stderr = process.stdout, process.stderr
    if stdout is None or stderr is None:
        raise RuntimeError("ripgrep has no pipes")
    entries: list[str] = []
    while wanted is None or len(entries) < wanted:
        line = await stdout.readline()
        if not line:
            return entries, (await stderr.read()).decode(errors="replace")
        entries.append(shown(line, label))
    return entries, ""


async def grep_files(place: Place, given: GrepArguments) -> Matches:
    if not place.target.exists():
        raise HTTPException(404, f"The store has no file or directory {place.path}.")
    target = f"{HERE}{place.inner}" if place.inner else "."
    end = given.offset + given.head_limit
    wanted = end if given.output_mode == "content" else None
    process = await asyncio.create_subprocess_exec(
        *ripgrep_command(given, target), cwd=place.root, stdin=DEVNULL, stdout=PIPE, stderr=PIPE
    )
    try:
        async with asyncio.timeout(GREP_SECONDS):
            entries, error = await collect(process, place.label, wanted)
    except TimeoutError as timeout:
        raise HTTPException(
            504, f"The grep took more than {GREP_SECONDS:g} s. Use a narrower pattern or path."
        ) from timeout
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    if error and not entries:
        raise HTTPException(400, f"Invalid grep: {error.removeprefix('rg: ').strip()}")
    return Matches(given.output_mode, entries[given.offset : end], len(entries))


def matching_paths(directory: Path, pattern: str) -> list[str]:
    return [path for path in file_paths(directory) if PurePosixPath(path).full_match(pattern)]


async def glob_files(place: Place, pattern: str, offset: int) -> list[str]:
    target = place.target
    if not target.is_dir():
        if target.exists():
            raise HTTPException(400, f"{place.path} is a file. Use read to read it.")
        raise HTTPException(404, f"The store has no directory {place.path}.")
    matched = await asyncio.to_thread(matching_paths, target, pattern)
    return [f"{place.path}/{path}" for path in matched[offset : offset + MAX_PATHS]]
