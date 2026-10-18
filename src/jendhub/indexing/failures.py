from collections import defaultdict
from dataclasses import dataclass

from jendhub.json import Json


@dataclass(frozen=True, order=True)
class Place:
    file: str
    line: int


@dataclass(frozen=True)
class LoadError:
    expected: str
    observed: str | None
    place: Place | None


@dataclass(frozen=True)
class Problem:
    expected: str
    observed: str | None
    places: list[Place]
    files: list[str]

    @property
    def message(self) -> str:
        if self.observed is None or self.observed.strip("'") in self.expected:
            return f"expected {self.expected}"
        return f"expected {self.expected}, got {self.observed}"


def problems(errors: dict[str, LoadError]) -> list[Problem]:
    places: defaultdict[tuple[str, str | None], set[Place]] = defaultdict(set)
    files: defaultdict[tuple[str, str | None], list[str]] = defaultdict(list)
    for file, error in sorted(errors.items()):
        key = (error.expected, error.observed)
        files[key].append(file)
        if error.place is not None:
            places[key].add(error.place)
    found = [
        Problem(expected, observed, sorted(places[expected, observed]), stopped)
        for (expected, observed), stopped in files.items()
    ]
    return sorted(found, key=lambda problem: (-len(problem.files), problem.files))


def summary(errors: dict[str, LoadError]) -> str:
    return "; ".join(
        f"{problem.message} ({len(problem.files)} {'file' if len(problem.files) == 1 else 'files'})"
        for problem in problems(errors)
    )


def errors_json(errors: dict[str, LoadError]) -> dict[str, Json]:
    return {
        file: {
            "expected": error.expected,
            "observed": error.observed,
            "file": None if error.place is None else error.place.file,
            "line": None if error.place is None else error.place.line,
        }
        for file, error in errors.items()
    }


def load_error(found: Json) -> LoadError:
    match found:
        case {"expected": str() as expected, "observed": str() | None as observed, **rest}:
            match rest:
                case {"file": str() as file, "line": int() as line}:
                    return LoadError(expected, observed, Place(file, line))
                case _:
                    return LoadError(expected, observed, None)
        case _:
            raise ValueError(f"not a load error: {found!r}")


def parse_errors(found: Json) -> dict[str, LoadError]:
    match found:
        case dict():
            return {file: load_error(error) for file, error in found.items()}
        case _:
            raise ValueError(f"not a map of load errors: {found!r}")
