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
class Highlight:
    start: int
    end: int
    kind: Kind
    declaration: bool


type LineHighlights = list[list[Highlight]]


def highlight(found: Json) -> Highlight:
    match found:
        case [int() as start, int() as end, str() as kind, bool() as declaration] if (
            0 <= start < end and kind in KINDS
        ):
            return Highlight(start, end, cast("Kind", kind), declaration)
        case _:
            raise HighlightError(f"invalid highlight: {found!r}")


def highlight_lines(found: Json) -> LineHighlights:
    match found:
        case list() as lines if all(isinstance(line, list) for line in lines):
            return [[highlight(item) for item in cast("list[Json]", line)] for line in lines]
        case _:
            raise HighlightError(f"invalid highlight lines: {found!r}")


def lines_json(lines: LineHighlights) -> Jsonb:
    return Jsonb(
        [[[item.start, item.end, item.kind, item.declaration] for item in line] for line in lines]
    )


def line_path(numbers: list[int]) -> str:
    return f"$[{', '.join(str(number) for number in numbers)}]"


async def read_line_highlights(
    connection: AsyncConnection, generation: int, release: str, module: str, numbers: list[int]
) -> list[list[Highlight]] | None:
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
    found = highlight_lines(row[0])
    return found if len(found) == len(numbers) else None


async def read_file_highlights(
    connection: AsyncConnection, generation: int, release: str, module: str
) -> LineHighlights | None:
    cursor = await connection.execute(
        "SELECT lines FROM highlights WHERE generation = %s AND release = %s AND module = %s",
        (generation, release, module),
    )
    row = await cursor.fetchone()
    return None if row is None else highlight_lines(row[0])


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
    return {id_: highlight_lines(lines) for id_, lines in await cursor.fetchall()}
