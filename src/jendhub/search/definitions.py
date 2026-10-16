from dataclasses import dataclass
from typing import LiteralString

from psycopg import sql

from jendhub.db.pool import Pool
from jendhub.search.sources import BASE_IMPORT, StoredRelease, package_source, used_imports


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
    used_by: list[str]
    used_by_total: int


USES: LiteralString = (
    "SELECT t.id FROM uses JOIN definitions t ON t.point = uses.target_point"
    " WHERE uses.user_point = %(point)s"
    " UNION SELECT t.id FROM external_uses e JOIN definitions t"
    " ON t.generation = e.generation AND t.key = e.target_key"
    " WHERE e.user_point = %(point)s"
)
USED_BY: LiteralString = (
    "SELECT u.id FROM uses JOIN definitions u ON u.point = uses.user_point"
    " WHERE uses.target_point = %(point)s"
    " UNION SELECT u.id FROM external_uses e JOIN definitions u ON u.point = e.user_point"
    " WHERE e.generation = %(generation)s AND e.target_key = %(key)s"
)


async def read_definition(
    pool: Pool, generation: int, id_: str, offset: int, limit: int
) -> DefinitionDetail | None:
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
        parameters = {"point": point, "generation": generation, "key": key}
        cursor = await connection.execute(
            sql.SQL("SELECT id FROM ({}) found ORDER BY id").format(sql.SQL(USES)), parameters
        )
        uses = [found for (found,) in await cursor.fetchall()]
        cursor = await connection.execute(
            sql.SQL("SELECT count(*) FROM ({}) found").format(sql.SQL(USED_BY)), parameters
        )
        total_row = await cursor.fetchone()
        cursor = await connection.execute(
            sql.SQL(
                "SELECT id FROM ({}) found ORDER BY id OFFSET %(offset)s LIMIT %(limit)s"
            ).format(sql.SQL(USED_BY)),
            parameters | {"offset": offset, "limit": limit},
        )
        used_by = [found for (found,) in await cursor.fetchall()]
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
        used_by,
        0 if total_row is None else total_row[0],
    )
