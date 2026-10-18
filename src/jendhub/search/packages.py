import re
from dataclasses import dataclass
from typing import Literal

from jendhub.db.pool import Pool
from jendhub.indexing.failures import Problem, parse_errors, problems
from jendhub.json import Json

type ReleaseState = Literal["indexed", "invalid", "unindexed"]

SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True)
class ReleaseDetail:
    version: str
    hash: str
    state: ReleaseState
    problems: list[Problem]


@dataclass(frozen=True)
class DefinitionSummary:
    id: str
    kind: str
    name: str
    module: str
    head: str

    @property
    def summary(self) -> str | None:
        words: list[str] = []
        for line in self.head.split("\n"):
            if not line.startswith("#"):
                break
            text = line.removeprefix("#").strip()
            if not text and words:
                break
            if text:
                words.append(text)
        paragraph = " ".join(words)
        found = SENTENCE_END.search(paragraph)
        return (paragraph if found is None else paragraph[: found.start() + 1]) or None


@dataclass(frozen=True)
class PackageDetail:
    name: str
    releases: list[ReleaseDetail]
    definitions: list[DefinitionSummary]
    definitions_total: int


@dataclass(frozen=True)
class PackageSummary:
    name: str
    version: str
    definitions: int


def release_state(errors: Json, *, indexed: bool) -> ReleaseState:
    if errors is not None:
        return "invalid"
    return "indexed" if indexed else "unindexed"


async def read_package(
    pool: Pool, generation: int, name: str, offset: int, limit: int
) -> PackageDetail | None:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT p.version, p.hash, i.errors, EXISTS ("
            "  SELECT 1 FROM definitions d WHERE d.generation = %s AND d.release = p.hash)"
            " FROM packages p LEFT JOIN invalid_releases i ON i.hash = p.hash"
            " WHERE p.name = %s ORDER BY p.version_order DESC",
            (generation, name),
        )
        rows: list[tuple[str, str, Json, bool]] = await cursor.fetchall()
        if not rows:
            return None
        releases = [
            ReleaseDetail(
                version,
                hash_,
                release_state(errors, indexed=indexed),
                [] if errors is None else problems(parse_errors(errors)),
            )
            for version, hash_, errors, indexed in rows
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


async def read_packages(
    pool: Pool, generation: int, offset: int, limit: int
) -> tuple[list[PackageSummary], int]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT count(DISTINCT release) FROM definitions"
            " WHERE generation = %s AND package IS NOT NULL",
            (generation,),
        )
        total_row = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT package, version, count(*) FROM definitions"
            " WHERE generation = %s AND package IS NOT NULL"
            " GROUP BY package, version ORDER BY package OFFSET %s LIMIT %s",
            (generation, offset, limit),
        )
        found = [PackageSummary(*row) for row in await cursor.fetchall()]
    return found, 0 if total_row is None else total_row[0]
