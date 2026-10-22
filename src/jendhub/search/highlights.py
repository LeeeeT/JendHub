from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from typing import Literal, cast, get_args

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

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


type LineHighlights = list[list[Highlight[str]]]


@dataclass(frozen=True)
class Extent:
    id: str
    first_line: int
    end_line: int


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


def target_key(found: Json) -> str | None:
    match found:
        case str() | None:
            return found
        case _:
            raise HighlightError(f"invalid highlight target: {found!r}")


def stored_lines(found: Json) -> LineHighlights:
    return highlight_lines(found, target_key)


def lines_json(lines: LineHighlights) -> Jsonb:
    return Jsonb(
        [
            [[item.start, item.end, item.kind, item.declaration, item.target] for item in line]
            for line in lines
        ]
    )


def targets(lines: Iterable[list[Highlight[str]]]) -> frozenset[str]:
    return frozenset(item.target for line in lines for item in line if item.target is not None)


async def read_extents(
    connection: AsyncConnection, generation: int, keys: Collection[str]
) -> dict[str, Extent]:
    if not keys:
        return {}
    cursor = await connection.execute(
        "SELECT key, id, first_line, end_line FROM definitions"
        " WHERE generation = %s AND key = ANY(%s)",
        (generation, sorted(keys)),
    )
    return {key: Extent(id_, first, end) for key, id_, first, end in await cursor.fetchall()}


def line_path(numbers: list[int]) -> str:
    return f"$[{', '.join(str(number) for number in numbers)}]"


async def read_line_highlights(
    connection: AsyncConnection, generation: int, release: str, module: str, numbers: list[int]
) -> LineHighlights | None:
    if not numbers:
        return []
    cursor = await connection.execute(
        "SELECT jsonb_path_query_array(lines, %s::jsonpath) FROM highlights"
        " WHERE generation = %s AND release = %s AND module = %s",
        (line_path(numbers), generation, release, module),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    found = stored_lines(row[0])
    return found if len(found) == len(numbers) else None


async def read_file_highlights(
    connection: AsyncConnection, generation: int, release: str, module: str
) -> LineHighlights | None:
    cursor = await connection.execute(
        "SELECT lines FROM highlights WHERE generation = %s AND release = %s AND module = %s",
        (generation, release, module),
    )
    row = await cursor.fetchone()
    return None if row is None else stored_lines(row[0])


async def read_head_highlights(
    connection: AsyncConnection, generation: int, ids: list[str], count: int
) -> dict[str, LineHighlights]:
    cursor = await connection.execute(
        "SELECT d.id, jsonb_path_query_array(h.lines, '$[$first to $last]',"
        " jsonb_build_object('first', d.first_line, 'last', d.first_line + %s - 1))"
        " FROM definitions d JOIN highlights h ON h.generation = d.generation"
        " AND h.release = d.release AND h.module = d.module"
        " WHERE d.generation = %s AND d.id = ANY(%s)",
        (count, generation, ids),
    )
    return {id_: stored_lines(lines) for id_, lines in await cursor.fetchall()}
