import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jendhub.errors import JendHubError
from jendhub.json import Json

INDEXER = "jendhub-index"


class UsesError(JendHubError):
    pass


@dataclass(frozen=True)
class Declaration:
    file: Path
    name: str


@dataclass(frozen=True)
class Use:
    user: Declaration
    target: Declaration


@dataclass(frozen=True)
class Failure:
    expected: str
    observed: str | None
    file: Path | None
    line: int | None


@dataclass(frozen=True)
class ResolvedFile:
    path: Path
    error: Failure | None
    declarations: frozenset[str]
    uses: list[Use]


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
        } if all(isinstance(name, str) for name in declarations):
            names = frozenset(name for name in declarations if isinstance(name, str))
            return ResolvedFile(
                Path(path), failure(error), names, [resolved_use(use) for use in uses]
            )
        case _:
            raise UsesError(f"{INDEXER} returned an invalid entry: {found!r}")


def resolve(lib: Path, base: Path, entries: list[Path]) -> list[ResolvedFile]:
    request = {
        "lib": str(lib),
        "base": str(base.resolve()),
        "entries": [str(entry) for entry in entries],
    }
    try:
        result = subprocess.run(  # noqa: S603
            [INDEXER], input=json.dumps(request), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise UsesError(f"{INDEXER} is not on PATH: run jendhub in nix develop") from error
    if result.returncode != 0:
        raise UsesError(f"{INDEXER} failed: {result.stderr.strip()}")
    answer: Json = json.loads(result.stdout)
    match answer:
        case {"entries": list() as found} if len(found) == len(entries):
            return [resolved_file(item) for item in found]
        case _:
            raise UsesError(f"{INDEXER} returned an invalid answer")
