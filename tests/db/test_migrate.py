from jendhub.db.migrate import migrate, migrations
from jendhub.db.pool import Pool


async def test_the_template_has_every_migration(pool: Pool) -> None:
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT name FROM schema_migrations ORDER BY name")
        assert [name for (name,) in await cursor.fetchall()] == [name for name, _ in migrations()]


async def test_migrate_applies_nothing_twice(pool: Pool) -> None:
    async with pool.connection() as connection:
        assert await migrate(connection) == []
