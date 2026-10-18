from dataclasses import dataclass
from typing import Literal

from jendhub.db.pool import Pool

type Client = Literal["agents", "web", "mcp"]


@dataclass(frozen=True)
class Search:
    client: Client
    user_agent: str | None
    query: str
    offset: int
    limit: int
    generation: int
    results: list[str]
    seconds: float


async def record_search(pool: Pool, search: Search) -> None:
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO searches (client, user_agent, query, page_offset, page_limit,"
            " generation, results, seconds) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                search.client,
                search.user_agent,
                search.query,
                search.offset,
                search.limit,
                search.generation,
                search.results,
                search.seconds,
            ),
        )
