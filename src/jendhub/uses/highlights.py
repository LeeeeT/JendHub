from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast, get_args

from jendhub.errors import JendHubError
from jendhub.json import Json

type Kind = Literal[
    "keyword",
    "comment",
    "string",
    "number",
    "operator",
    "namespace",
    "variable",
    "function",
    "type",
    "constructor",
]

KINDS: frozenset[str] = frozenset(get_args(Kind.__value__))


class HighlightError(JendHubError):
    pass


@dataclass(frozen=True)
class Highlight[T]:
    start: int
    end: int
    kind: Kind
    declaration: bool
    target: T | None


def highlight[T](found: Json, target: Callable[[Json], T | None]) -> Highlight[T]:
    match found:
        case [int() as start, int() as end, str() as kind, bool() as declaration, found_target] if (
            0 <= start < end and kind in KINDS
        ):
            return Highlight(start, end, cast("Kind", kind), declaration, target(found_target))
        case _:
            raise HighlightError(f"invalid highlight: {found!r}")


def highlight_lines[T](found: Json, target: Callable[[Json], T | None]) -> list[list[Highlight[T]]]:
    match found:
        case list() as lines if all(isinstance(line, list) for line in lines):
            return [
                [highlight(item, target) for item in cast("list[Json]", line)] for line in lines
            ]
        case _:
            raise HighlightError(f"invalid highlight lines: {found!r}")
