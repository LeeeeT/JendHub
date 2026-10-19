from dataclasses import dataclass

from jendhub.db.pool import Pool
from jendhub.hub.library import Library
from jendhub.search.sources import BASE_IMPORT, StoredRelease, package_source, used_imports


@dataclass(frozen=True)
class DefinitionDetail:
    id: str
    key: str
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


async def read_definition(
    pool: Pool, library: Library, generation: int, id_: str
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
            source = await package_source(connection, library, stored, f"{module}.bend")
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
    return DefinitionDetail(
        id_, key, kind, name, package, version, module, text, imports, first_line, end_line, uses
    )
