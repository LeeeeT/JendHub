from dataclasses import dataclass

from psycopg import AsyncConnection, sql

from jendhub.db.pool import Pool
from jendhub.search.engine import Hit
from jendhub.search.sources import BASE, BASE_RELEASE
from jendhub.vectors.index import Scope

LARGEST_READ = 10_000
LISTED = sql.SQL(
    "SELECT d.id, d.key, d.kind, d.name, d.package, d.version, d.module, d.head, c.users"
)
COUNTED = sql.SQL("JOIN user_counts c ON c.generation = d.generation AND c.target_key = d.key")


@dataclass(frozen=True)
class Filters:
    packages: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    used_by: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.packages or self.uses or self.used_by)


@dataclass(frozen=True)
class Used:
    key: str
    point: int
    users: int


@dataclass(frozen=True)
class Resolved:
    generation: int
    releases: frozenset[str]
    used: tuple[Used, ...]
    targets: frozenset[int] | None

    @property
    def empty(self) -> bool:
        return len(self.releases) > 1 or self.targets == frozenset()

    @property
    def scope(self) -> Scope:
        release = next(iter(self.releases), None)
        return Scope(release, tuple(found.key for found in self.used), self.targets)


class UnknownFilterError(Exception):
    pass


def unknown_definition(key: str) -> UnknownFilterError:
    return UnknownFilterError(
        f"The index has no definition {key}. Name a definition as package/module:name,"
        " for example bend-kit-json/json:parse.bytes, or by its name in Base, for example"
        " List.map."
    )


async def package_release(connection: AsyncConnection, generation: int, package: str) -> str:
    if package == BASE:
        return BASE_RELEASE
    cursor = await connection.execute(
        "SELECT p.hash FROM packages p WHERE p.name = %s AND EXISTS ("
        " SELECT FROM definitions d WHERE d.generation = %s AND d.release = p.hash)",
        (package, generation),
    )
    row = await cursor.fetchone()
    if row is None:
        raise UnknownFilterError(f"The index has no package {package}.")
    return row[0]


async def used_definition(connection: AsyncConnection, generation: int, key: str) -> Used:
    cursor = await connection.execute(
        sql.SQL(
            "SELECT d.point, c.users FROM definitions d {} WHERE d.generation = %s AND d.key = %s"
        ).format(COUNTED),
        (generation, key),
    )
    row = await cursor.fetchone()
    if row is None:
        raise unknown_definition(key)
    return Used(key, *row)


async def used_points(connection: AsyncConnection, generation: int, key: str) -> frozenset[int]:
    cursor = await connection.execute(
        "SELECT point FROM definitions WHERE generation = %s AND key = %s", (generation, key)
    )
    row = await cursor.fetchone()
    if row is None:
        raise unknown_definition(key)
    cursor = await connection.execute(
        "SELECT target_point FROM uses WHERE user_point = %(user)s"
        " UNION SELECT t.point FROM external_uses e JOIN definitions t"
        " ON t.generation = e.generation AND t.key = e.target_key"
        " WHERE e.user_point = %(user)s",
        {"user": row[0]},
    )
    return frozenset(point for (point,) in await cursor.fetchall())


async def resolve(pool: Pool, generation: int, filters: Filters) -> Resolved:
    async with pool.connection() as connection:
        releases = [
            await package_release(connection, generation, name) for name in filters.packages
        ]
        used = [await used_definition(connection, generation, key) for key in filters.uses]
        targets: frozenset[int] | None = None
        for key in filters.used_by:
            found = await used_points(connection, generation, key)
            targets = found if targets is None else targets & found
    return Resolved(generation, frozenset(releases), tuple(used), targets)


async def users_by_key(pool: Pool, generation: int, keys: list[str]) -> dict[str, int]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT target_key, users FROM user_counts WHERE generation = %s"
            " AND target_key = ANY(%s)",
            (generation, keys),
        )
        return dict(await cursor.fetchall())


type Arguments = dict[str, object]


def uses_condition(index: int) -> sql.Composable:
    return sql.SQL(
        "(EXISTS (SELECT FROM uses WHERE user_point = d.point AND target_point = {point})"
        " OR EXISTS (SELECT FROM external_uses WHERE user_point = d.point AND target_key = {key}))"
    ).format(point=sql.Placeholder(f"point{index}"), key=sql.Placeholder(f"key{index}"))


def users_query(index: int) -> sql.Composable:
    return sql.SQL(
        "SELECT user_point AS point FROM uses WHERE target_point = {point}"
        " UNION ALL SELECT user_point FROM external_uses"
        " WHERE generation = %(generation)s AND target_key = {key}"
    ).format(point=sql.Placeholder(f"point{index}"), key=sql.Placeholder(f"key{index}"))


RELEASE_CONDITION = sql.SQL("d.release = %(release)s")
TARGETS_CONDITION = sql.SQL("d.point = ANY(%(targets)s)")
RELEASE_QUERY = sql.SQL(
    "SELECT point FROM definitions WHERE generation = %(generation)s AND release = %(release)s"
)
TARGETS_QUERY = sql.SQL("SELECT unnest(%(targets)s::bigint[]) AS point")


async def release_size(connection: AsyncConnection, generation: int, release: str) -> int:
    cursor = await connection.execute(
        "SELECT count(*) FROM definitions WHERE generation = %s AND release = %s",
        (generation, release),
    )
    row = await cursor.fetchone()
    return 0 if row is None else row[0]


@dataclass(frozen=True)
class Narrowing:
    size: int
    matches: sql.Composable
    condition: sql.Composable


async def narrowings(
    connection: AsyncConnection, found: Resolved, arguments: Arguments
) -> list[Narrowing]:
    narrowed: list[Narrowing] = []
    for index, used in enumerate(found.used):
        arguments[f"point{index}"] = used.point
        arguments[f"key{index}"] = used.key
        narrowed.append(Narrowing(used.users, users_query(index), uses_condition(index)))
    for release in found.releases:
        arguments["release"] = release
        size = await release_size(connection, found.generation, release)
        narrowed.append(Narrowing(size, RELEASE_QUERY, RELEASE_CONDITION))
    if found.targets is not None:
        arguments["targets"] = sorted(found.targets)
        narrowed.append(Narrowing(len(found.targets), TARGETS_QUERY, TARGETS_CONDITION))
    return narrowed


async def listing(
    connection: AsyncConnection, found: Resolved, limit: int
) -> tuple[sql.Composed, Arguments]:
    arguments: Arguments = {"generation": found.generation, "limit": limit}
    narrowed = await narrowings(connection, found, arguments)
    driver = min(narrowed, key=lambda narrowing: narrowing.size, default=None)
    if driver is None or driver.size > LARGEST_READ:
        query = sql.SQL(
            "{listed} FROM user_counts c JOIN definitions d"
            " ON d.generation = c.generation AND d.key = c.target_key"
            " WHERE c.generation = %(generation)s{conditions}"
            " ORDER BY c.users DESC, c.target_key LIMIT %(limit)s"
        ).format(
            listed=LISTED,
            conditions=sql.SQL("").join(
                sql.SQL(" AND {}").format(narrowing.condition) for narrowing in narrowed
            ),
        )
        return query, arguments
    others = [narrowing.condition for narrowing in narrowed if narrowing is not driver]
    query = sql.SQL(
        "WITH found AS MATERIALIZED ({matches})"
        " {listed} FROM found JOIN definitions d ON d.point = found.point {counted}{where}"
        " ORDER BY c.users DESC, d.key LIMIT %(limit)s"
    ).format(
        matches=driver.matches,
        listed=LISTED,
        counted=COUNTED,
        where=sql.SQL(" WHERE {}").format(sql.SQL(" AND ").join(others)) if others else sql.SQL(""),
    )
    return query, arguments


async def most_used(pool: Pool, found: Resolved, limit: int) -> list[Hit]:
    if found.empty:
        return []
    async with pool.connection() as connection:
        query, arguments = await listing(connection, found, limit)
        cursor = await connection.execute(query, arguments)
        rows = await cursor.fetchall()
    return [
        Hit(id_, key, kind, name, package, version, module, head, float(users))
        for id_, key, kind, name, package, version, module, head, users in rows
    ]
