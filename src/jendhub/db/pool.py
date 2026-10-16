from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from jendhub.settings import DatabaseSettings

type Pool = AsyncConnectionPool[AsyncConnection]


@asynccontextmanager
async def open_pool(settings: DatabaseSettings) -> AsyncGenerator[Pool]:
    pool: Pool = AsyncConnectionPool(
        settings.database_url, min_size=1, max_size=settings.database_pool_size, open=False
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()
