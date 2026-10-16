from dataclasses import dataclass
from typing import Literal

from jendhub.db.pool import Pool

type ReleaseState = Literal["indexed", "invalid", "unindexed"]


@dataclass(frozen=True)
class ReleaseDetail:
    version: str
    hash: str
    state: ReleaseState
    reason: str | None


@dataclass(frozen=True)
class DefinitionSummary:
    id: str
    kind: str
    name: str
    module: str
    head: str


@dataclass(frozen=True)
class PackageDetail:
    name: str
    releases: list[ReleaseDetail]
    definitions: list[DefinitionSummary]
    definitions_total: int


def release_state(reason: str | None, *, indexed: bool) -> ReleaseState:
    if reason is not None:
        return "invalid"
    return "indexed" if indexed else "unindexed"


async def read_package(
    pool: Pool, generation: int, name: str, offset: int, limit: int
) -> PackageDetail | None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT p.version, p.hash, i.reason, EXISTS ("
            "  SELECT 1 FROM definitions d WHERE d.generation = %s AND d.release = p.hash)"
            " FROM packages p LEFT JOIN invalid_releases i ON i.hash = p.hash"
            " WHERE p.name = %s ORDER BY p.version_order DESC",
            (generation, name),
        )
        rows: list[tuple[str, str, str | None, bool]] = await cursor.fetchall()
        if not rows:
            return None
        releases = [
            ReleaseDetail(version, hash_, release_state(reason, indexed=indexed), reason)
            for version, hash_, reason, indexed in rows
        ]
        indexed = [release.hash for release in releases if release.state == "indexed"]
        cursor = await connection.execute(
            "SELECT count(*) FROM definitions WHERE generation = %s AND release = ANY(%s)",
            (generation, indexed),
        )
        total_row = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT id, kind, name, module, head FROM definitions"
            " WHERE generation = %s AND release = ANY(%s)"
            " ORDER BY module, first_line OFFSET %s LIMIT %s",
            (generation, indexed, offset, limit),
        )
        definitions = [DefinitionSummary(*row) for row in await cursor.fetchall()]
    return PackageDetail(name, releases, definitions, 0 if total_row is None else total_row[0])
