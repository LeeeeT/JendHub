from dataclasses import dataclass
from typing import Literal

from jendhub.db.pool import Pool
from jendhub.search.filters import Filters

type Client = Literal["agents", "web", "mcp"]


@dataclass(frozen=True)
class Caller:
    client: Client
    user_agent: str | None
    name: str | None = None


@dataclass(frozen=True)
class Search:
    caller: Caller
    query: str
    filters: Filters
    offset: int
    limit: int
    generation: int
    results: list[str]
    seconds: float


async def record_search(pool: Pool, search: Search) -> None:
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO searches (client, user_agent, client_name, query, packages, uses,"
            " used_by, page_offset, page_limit, generation, results, seconds)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                search.caller.client,
                search.caller.user_agent,
                search.caller.name,
                search.query,
                list(search.filters.packages),
                list(search.filters.uses),
                list(search.filters.used_by),
                search.offset,
                search.limit,
                search.generation,
                search.results,
                search.seconds,
            ),
        )
