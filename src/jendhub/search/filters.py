from dataclasses import dataclass
from typing import LiteralString

from psycopg import AsyncConnection

from jendhub.db.pool import Pool
from jendhub.search.sources import BASE


@dataclass(frozen=True)
class Filters:
    packages: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    used_in: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.packages or self.uses or self.used_in)

    def words(self) -> list[str]:
        return [
            *(f"package={package}" for package in self.packages),
            *(f"uses={key}" for key in self.uses),
            *(f"used_in={key}" for key in self.used_in),
        ]


class UnknownFilterError(Exception):
    pass


async def points(
    connection: AsyncConnection, query: LiteralString, arguments: dict[str, object]
) -> set[int]:
    cursor = await connection.execute(query, arguments)
    return {point for (point,) in await cursor.fetchall()}


async def package_points(connection: AsyncConnection, generation: int, package: str) -> set[int]:
    found = await points(
        connection,
        "SELECT point FROM definitions WHERE generation = %(generation)s"
        " AND package IS NOT DISTINCT FROM %(package)s",
        {"generation": generation, "package": None if package == BASE else package},
    )
    if not found:
        raise UnknownFilterError(f"The index has no package {package}.")
    return found


async def definition_point(connection: AsyncConnection, generation: int, key: str) -> int:
    cursor = await connection.execute(
        "SELECT point FROM definitions WHERE generation = %s AND key = %s", (generation, key)
    )
    row = await cursor.fetchone()
    if row is None:
        raise UnknownFilterError(
            f"The index has no definition {key}. Name a definition as package/module:name,"
            " for example bend-kit-json/json:parse.bytes, or by its name in Base, for example"
            " List.map."
        )
    return row[0]


async def user_points(connection: AsyncConnection, generation: int, key: str) -> set[int]:
    target = await definition_point(connection, generation, key)
    return await points(
        connection,
        "SELECT user_point FROM uses WHERE target_point = %(target)s"
        " UNION SELECT user_point FROM external_uses"
        " WHERE generation = %(generation)s AND target_key = %(key)s",
        {"target": target, "generation": generation, "key": key},
    )


async def used_points(connection: AsyncConnection, generation: int, key: str) -> set[int]:
    user = await definition_point(connection, generation, key)
    return await points(
        connection,
        "SELECT target_point FROM uses WHERE user_point = %(user)s"
        " UNION SELECT t.point FROM external_uses e JOIN definitions t"
        " ON t.generation = e.generation AND t.key = e.target_key"
        " WHERE e.user_point = %(user)s",
        {"user": user},
    )


async def filtered_points(pool: Pool, generation: int, filters: Filters) -> list[int]:
    async with pool.connection() as connection:
        found: list[set[int]] = [
            *[await package_points(connection, generation, name) for name in filters.packages],
            *[await user_points(connection, generation, key) for key in filters.uses],
            *[await used_points(connection, generation, key) for key in filters.used_in],
        ]
    return sorted(found[0].intersection(*found[1:])) if found else []
