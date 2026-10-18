from jendhub.db.migrate import migrate
from jendhub.db.pool import Pool


async def test_migrate_applies_nothing_twice(pool: Pool) -> None:
    async with pool.connection() as connection:
        assert await migrate(connection) == []
