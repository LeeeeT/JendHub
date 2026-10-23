import asyncio
from asyncio.subprocess import DEVNULL, PIPE, Process
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from psycopg import AsyncConnection

from jendhub.db.pool import Pool
from jendhub.hub.api import HASH, valid_path
from jendhub.hub.library import Library
from jendhub.search.sources import BASE, base_tag, latest_releases, read_text, stored_release
from jendhub.services.site.arguments import (
    MAX_COLUMNS,
    MAX_LINES,
    FilesArguments,
    GrepArguments,
)

RIPGREP_SECONDS = 10.0
RIPGREP = "rg"
RIPGREP_FLAGS = [
    "--no-config",
    "--no-messages",
    "--color=never",
    "--sort=path",
    "--hidden",
    "--no-ignore",
]
HERE = "./"
NAMED_RELEASES = 5
NOT_READY = "The search index is not ready. Try again later."


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
    first: int
    lines: list[str]
    total: int
    cut: bool

    @property
    def last(self) -> int:
        return self.first + len(self.lines) - 1


@dataclass(frozen=True)
class Page:
    entries: list[str]
    offset: int
    more: bool


async def unknown_release(connection: AsyncConnection, release: str) -> HTTPException:
    if HASH.fullmatch(release):
        return HTTPException(404, f"The store has no release with the hash “{release}”.")
    name = release.partition("@")[0]
    found, total = await latest_releases(connection, name, NAMED_RELEASES)
    if not found:
        return HTTPException(
            404,
            f"The store has no package “{name}”. Start a path with a release as name@version,"
            " for example kit@1.0.0.0, or with the hash of a release.",
        )
    more = f" and {total - len(found)} more" if total > len(found) else ""
    return HTTPException(
        404,
        f"The store has no release “{release}”. The latest releases of {name} are"
        f" {', '.join(found)}{more}.",
    )


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
            raise await unknown_release(connection, release)
    return Place(stored.label, library.package_directory(stored.hash), inner)


def split_lines(text: str) -> list[str]:
    return text.removesuffix("\n").split("\n") if text else []


async def read_lines(place: Place, start_line: int, end_line: int | None) -> Lines:
    if place.target.is_dir():
        raise HTTPException(400, f"{place.path} is a directory. Use `files` to list its files.")
    text = await asyncio.to_thread(read_text, place.target)
    if text is None:
        raise HTTPException(404, f"The store has no file “{place.path}”.")
    lines = split_lines(text)
    if lines and start_line > len(lines):
        raise HTTPException(400, f"{place.path} has {len(lines)} lines.")
    if end_line is not None and end_line < start_line:
        raise HTTPException(400, "end_line must not be less than start_line.")
    wanted = min(len(lines), end_line or len(lines))
    last = min(wanted, start_line + MAX_LINES - 1)
    return Lines(place.path, start_line, lines[start_line - 1 : last], len(lines), last < wanted)


def glob_flags(glob: str | None) -> list[str]:
    return [] if glob is None else [f"--glob={glob}"]


def grep_command(given: GrepArguments, target: str) -> list[str]:
    command = [
        RIPGREP,
        *RIPGREP_FLAGS,
        "--no-heading",
        "--with-filename",
        f"--max-columns={MAX_COLUMNS}",
        "--max-columns-preview",
        *glob_flags(given.glob),
    ]
    if given.ignore_case:
        command.append("--ignore-case")
    if given.multiline:
        command += ["--multiline", "--multiline-dotall"]
    match given.mode:
        case "files":
            command.append("--files-with-matches")
        case "counts":
            command.append("--count")
        case "lines":
            command.append("--line-number")
            for flag, lines in [
                ("--context", given.context),
                ("--before-context", given.before),
                ("--after-context", given.after),
            ]:
                if lines is not None:
                    command.append(f"{flag}={lines}")
    return [*command, f"--regexp={given.pattern}", "--", target]


def files_command(given: FilesArguments, target: str) -> list[str]:
    return [RIPGREP, *RIPGREP_FLAGS, "--files", *glob_flags(given.glob), "--", target]


def shown(line: bytes, label: str) -> str:
    text = line.decode(errors="replace").removesuffix("\n")
    return f"{label}/{text.removeprefix(HERE)}" if text.startswith(HERE) else text


def ripgrep_target(place: Place) -> str:
    return f"{HERE}{place.inner}" if place.inner else "."


async def collect(process: Process, label: str, wanted: int) -> tuple[list[str], str]:
    stdout, stderr = process.stdout, process.stderr
    if stdout is None or stderr is None:
        raise RuntimeError("ripgrep has no pipes")
    entries: list[str] = []
    while len(entries) < wanted:
        line = await stdout.readline()
        if not line:
            return entries, (await stderr.read()).decode(errors="replace")
        entries.append(shown(line, label))
    return entries, ""


async def ripgrep(place: Place, command: list[str], limit: int, offset: int) -> Page:
    end = offset + limit
    process = await asyncio.create_subprocess_exec(
        *command, cwd=place.root, stdin=DEVNULL, stdout=PIPE, stderr=PIPE
    )
    try:
        async with asyncio.timeout(RIPGREP_SECONDS):
            entries, error = await collect(process, place.label, end + 1)
    except TimeoutError as timeout:
        raise HTTPException(
            504,
            f"ripgrep took more than {RIPGREP_SECONDS:g} s. Use a narrower path, glob or pattern.",
        ) from timeout
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    if error and not entries:
        raise HTTPException(400, f"Invalid argument: {error.removeprefix('rg: ').strip()}")
    return Page(entries[offset:end], offset, len(entries) > end)


async def grep_files(place: Place, given: GrepArguments) -> Page:
    if not place.target.exists():
        raise HTTPException(404, f"The store has no file or directory “{place.path}”.")
    return await ripgrep(
        place, grep_command(given, ripgrep_target(place)), given.limit, given.offset
    )


async def list_files(place: Place, given: FilesArguments) -> Page:
    if not place.target.is_dir():
        if place.target.exists():
            raise HTTPException(400, f"{place.path} is a file. Use `read` to read it.")
        raise HTTPException(404, f"The store has no directory “{place.path}”.")
    return await ripgrep(
        place, files_command(given, ripgrep_target(place)), given.limit, given.offset
    )
