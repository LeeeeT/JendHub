from dataclasses import dataclass

from psycopg import AsyncConnection

from jendhub.db.pool import Pool
from jendhub.search.sources import BASE_IMPORT, StoredRelease, package_source, used_imports

USERS = 100


@dataclass(frozen=True)
class DefinitionDetail:
    id: str
    kind: str
    name: str
    package: str | None
    version: str | None
    module: str
    text: str
    imports: list[str]
    first_line: int
    end_line: int
    uses: list[str]
    users: list[str]
    users_total: int


async def read_users(
    connection: AsyncConnection, generation: int, point: int, key: str, limit: int
) -> tuple[list[str], int]:
    cursor = await connection.execute(
        "WITH found AS ("
        " SELECT u.id FROM uses JOIN definitions u ON u.point = uses.user_point"
        " WHERE uses.target_point = %(point)s"
        " UNION SELECT u.id FROM external_uses e JOIN definitions u ON u.point = e.user_point"
        " WHERE e.generation = %(generation)s AND e.target_key = %(key)s)"
        " SELECT id, count(*) OVER () FROM found ORDER BY id LIMIT %(limit)s",
        {"point": point, "generation": generation, "key": key, "limit": limit},
    )
    rows = await cursor.fetchall()
    return [id_ for id_, _ in rows], rows[0][1] if rows else 0


async def read_definition(pool: Pool, generation: int, id_: str) -> DefinitionDetail | None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT point, release, key, kind, name, package, version, module, text,"
            " first_line, end_line FROM definitions WHERE generation = %s AND id = %s",
            (generation, id_),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        point, release, key, kind, name, package, version, module, text, first_line, end_line = row
        if package is None:
            imports = [BASE_IMPORT]
        else:
            stored = StoredRelease(f"{package}@{version}", release)
            source = await package_source(connection, stored, f"{module}.bend")
            imports = [] if source is None else used_imports(source, text)
        cursor = await connection.execute(
            "SELECT t.id FROM uses JOIN definitions t ON t.point = uses.target_point"
            " WHERE uses.user_point = %(point)s"
            " UNION SELECT t.id FROM external_uses e JOIN definitions t"
            " ON t.generation = e.generation AND t.key = e.target_key"
            " WHERE e.user_point = %(point)s ORDER BY 1",
            {"point": point},
        )
        uses = [found for (found,) in await cursor.fetchall()]
        users, users_total = await read_users(connection, generation, point, key, USERS)
    return DefinitionDetail(
        id_,
        kind,
        name,
        package,
        version,
        module,
        text,
        imports,
        first_line,
        end_line,
        uses,
        users,
        users_total,
    )
