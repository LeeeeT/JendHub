from dataclasses import dataclass
from datetime import datetime

from jendhub.db.pool import Pool
from jendhub.indexing.failures import Problem, parse_errors, problems
from jendhub.indexing.store import GenerationState
from jendhub.vectors.index import VectorConfig


@dataclass(frozen=True)
class JobCounts:
    queued: int
    running: int
    done: int
    superseded: int
    invalid: int
    failed: int


@dataclass(frozen=True)
class GenerationStatus:
    id: int
    corpus: str
    state: GenerationState
    base_tag: str
    vectors: str
    indexer_version: int
    created_at: datetime
    releases: int
    definitions: int
    jobs: JobCounts


@dataclass(frozen=True)
class InvalidRelease:
    name: str | None
    version: str | None
    hash: str
    base_tag: str
    problems: list[Problem]
    found_at: datetime

    @property
    def label(self) -> str:
        return self.hash if self.name is None else f"{self.name}@{self.version}"


async def generation_statuses(pool: Pool, corpus: str | None) -> list[GenerationStatus]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT g.id, g.corpus, g.state, g.base_tag, g.dimensions, g.quantization,"
            " g.indexer_version, g.created_at,"
            " size.releases, size.definitions,"
            " count(j.release) FILTER (WHERE j.state = 'queued'),"
            " count(j.release) FILTER (WHERE j.state = 'running'),"
            " count(j.release) FILTER (WHERE j.state = 'done'),"
            " count(j.release) FILTER (WHERE j.state = 'superseded'),"
            " count(j.release) FILTER (WHERE j.state = 'invalid'),"
            " count(j.release) FILTER (WHERE j.state = 'failed')"
            " FROM generations g"
            " CROSS JOIN LATERAL ("
            "   SELECT (SELECT count(DISTINCT h.release) FROM highlights h"
            "   WHERE h.generation = g.id) AS releases,"
            "   (SELECT count(*) FROM definitions d WHERE d.generation = g.id) AS definitions"
            " ) size"
            " LEFT JOIN index_jobs j ON j.generation = g.id"
            " WHERE g.state <> 'retired' AND (%(corpus)s::text IS NULL OR g.corpus = %(corpus)s)"
            " GROUP BY g.id, size.releases, size.definitions ORDER BY g.id",
            {"corpus": corpus},
        )
        rows = await cursor.fetchall()
    return [
        GenerationStatus(
            id_,
            corpus_,
            state,
            base_tag,
            VectorConfig(dimensions, quantization).label,
            indexer_version,
            created_at,
            releases,
            definitions,
            JobCounts(*jobs),
        )
        for (
            id_,
            corpus_,
            state,
            base_tag,
            dimensions,
            quantization,
            indexer_version,
            created_at,
            releases,
            definitions,
            *jobs,
        ) in rows
    ]


async def invalid_releases(pool: Pool, offset: int, limit: int) -> tuple[list[InvalidRelease], int]:
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT count(*) FROM invalid_releases")
        total_row = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT p.name, p.version, i.hash, i.base_tag, i.errors, i.found_at"
            " FROM invalid_releases i JOIN packages p ON p.hash = i.hash"
            " ORDER BY i.found_at DESC, p.name, p.version_order DESC OFFSET %s LIMIT %s",
            (offset, limit),
        )
        found = [
            InvalidRelease(name, version, hash_, base_tag, problems(parse_errors(errors)), found_at)
            for name, version, hash_, base_tag, errors, found_at in await cursor.fetchall()
        ]
    return found, 0 if total_row is None else total_row[0]
