from importlib.resources import files

from psycopg import AsyncConnection

MIGRATIONS = files("jendhub.db").joinpath("migrations")
LOCK = 0x4A454E44


def migrations() -> list[tuple[str, str]]:
    found = [entry for entry in MIGRATIONS.iterdir() if entry.name.endswith(".sql")]
    return [(entry.name, entry.read_text(encoding="utf-8")) for entry in sorted(found, key=str)]


async def migrate(connection: AsyncConnection) -> list[str]:
    applied: list[str] = []
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cursor = await connection.execute("SELECT name FROM schema_migrations")
        done = {name for (name,) in await cursor.fetchall()}
        for name, script in migrations():
            if name in done:
                continue
            await connection.execute(script.encode())
            await connection.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied
