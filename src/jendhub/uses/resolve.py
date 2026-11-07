import asyncio
import json
import tempfile
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from jendhub.errors import JendHubError
from jendhub.json import Json
from jendhub.uses.highlights import Highlight, HighlightError, highlight_lines

INDEXER = "jendhub-index"


class UsesError(JendHubError):
    pass


@dataclass(frozen=True, slots=True)
class Declaration:
    file: Path
    name: str


@dataclass(frozen=True, slots=True)
class Use:
    user: Declaration
    target: Declaration


@dataclass(frozen=True, slots=True)
class Failure:
    expected: str
    observed: str | None
    file: Path | None
    line: int | None


@dataclass(frozen=True, slots=True)
class ResolvedFile:
    path: Path
    error: Failure | None
    declarations: frozenset[str]
    uses: list[Use]
    highlights: list[list[Highlight[Declaration]]]


def declaration(found: Json) -> Declaration:
    match found:
        case {"file": str() as file, "name": str() as name}:
            return Declaration(Path(file), name)
        case _:
            raise UsesError(f"{INDEXER} returned an invalid declaration: {found!r}")


def resolved_use(found: Json) -> Use:
    match found:
        case {"from": user, "to": target}:
            return Use(declaration(user), declaration(target))
        case _:
            raise UsesError(f"{INDEXER} returned an invalid use: {found!r}")


def target_of(targets: list[Declaration]) -> Callable[[Json], Declaration | None]:
    def target(found: Json) -> Declaration | None:
        match found:
            case None:
                return None
            case int() as index if 0 <= index < len(targets):
                return targets[index]
            case _:
                raise HighlightError(f"invalid highlight target: {found!r}")

    return target


def failure(found: Json) -> Failure | None:
    match found:
        case None:
            return None
        case {
            "expected": str() as expected,
            "observed": str() | None as observed,
            "file": str() | None as file,
            "line": int() | None as line,
        }:
            return Failure(expected, observed, None if file is None else Path(file), line)
        case _:
            raise UsesError(f"{INDEXER} returned an invalid error: {found!r}")


def resolved_file(found: Json) -> ResolvedFile:
    match found:
        case {
            "path": str() as path,
            "error": error,
            "declarations": list() as declarations,
            "uses": list() as uses,
            "highlights": highlights,
            "targets": list() as targets,
        } if all(isinstance(name, str) for name in declarations):
            names = frozenset(name for name in declarations if isinstance(name, str))
            try:
                lines = highlight_lines(highlights, target_of([declaration(t) for t in targets]))
            except HighlightError as error:
                raise UsesError(f"{INDEXER} returned {error}") from error
            return ResolvedFile(
                Path(path), failure(error), names, [resolved_use(use) for use in uses], lines
            )
        case _:
            raise UsesError(f"{INDEXER} returned an invalid entry: {found!r}")


def read_entries(output: TextIO, entries: list[Path]) -> Iterator[ResolvedFile]:
    for path in entries:
        try:
            found = resolved_file(json.loads(output.readline()))
        except ValueError as error:
            raise UsesError(f"{INDEXER} returned invalid JSON") from error
        if found.path != path:
            raise UsesError(f"{INDEXER} returned an unexpected entry: {found.path}")
        yield found
    if output.read(1):
        raise UsesError(f"{INDEXER} returned too many entries")


@asynccontextmanager
async def resolve(
    lib: Path, base: Path, entries: list[Path]
) -> AsyncGenerator[Iterator[ResolvedFile]]:
    request = {
        "lib": str(lib),
        "base": str(await asyncio.to_thread(base.resolve)),
        "entries": [str(entry) for entry in entries],
    }
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors,
    ):
        try:
            process = await asyncio.create_subprocess_exec(
                INDEXER, stdin=asyncio.subprocess.PIPE, stdout=output, stderr=errors
            )
        except FileNotFoundError as error:
            raise UsesError(f"{INDEXER} is not on PATH: run jendhub in nix develop") from error
        try:
            await process.communicate(json.dumps(request).encode())
            if process.returncode != 0:
                errors.seek(0)
                raise UsesError(f"{INDEXER} failed: {errors.read().strip()}")
            output.seek(0)
            yield read_entries(output, entries)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
