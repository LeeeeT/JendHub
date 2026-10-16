from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb

from jendhub.corpus.definitions import Definition, Origin
from jendhub.db.pool import Pool
from jendhub.embedding.model import digest
from jendhub.errors import JendHubError
from jendhub.hub.api import Release
from jendhub.indexing.artifacts import Artifacts
from jendhub.vectors.index import Quantization, VectorConfig, collection_name

INDEXER_VERSION = 2
LIVE = "live"
MAX_ATTEMPTS = 5
RETRY_BASE = timedelta(minutes=1)
RETRY_LIMIT = timedelta(hours=1)

type GenerationState = Literal["building", "live", "retired"]


class IndexStoreError(JendHubError):
    pass


@dataclass(frozen=True)
class Generation:
    id: int
    corpus: str
    base_tag: str
    indexer_version: int
    state: GenerationState
    config: VectorConfig

    @property
    def collection(self) -> str:
        return collection_name(self.id, self.config)


@dataclass(frozen=True)
class Job:
    generation: int
    release: str
    name: str | None
    attempts: int


@dataclass(frozen=True)
class Written:
    points: list[int]
    removed: list[str]
    keys: list[str]


@dataclass(frozen=True)
class Superseded:
    by: str


def source_order(definition: Definition) -> tuple[bool, str, str, int]:
    release = definition.origin.release
    module = f"{definition.origin.module}.bend"
    return (release is not None, str(release or ""), module, definition.start)


GENERATION_COLUMNS = sql.SQL(", ").join(
    map(
        sql.Identifier,
        ["id", "corpus", "base_tag", "indexer_version", "state", "dimensions", "quantization"],
    )
)

type GenerationRow = tuple[int, str, str, int, GenerationState, int, Quantization]


def generation(row: GenerationRow) -> Generation:
    id_, corpus, base_tag, indexer_version, state, dimensions, quantization = row
    config = VectorConfig(dimensions, quantization)
    return Generation(id_, corpus, base_tag, indexer_version, state, config)


class IndexStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def create_generation(
        self, corpus: str, base_tag: str, config: VectorConfig
    ) -> Generation:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "INSERT INTO generations"
                    " (corpus, base_tag, indexer_version, dimensions, quantization)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING {}"
                ).format(GENERATION_COLUMNS),
                (corpus, base_tag, INDEXER_VERSION, config.dimensions, config.quantization),
            )
            row = await cursor.fetchone()
        if row is None:
            raise IndexStoreError("the new generation has no row")
        return generation(row)

    async def generation(self, generation_id: int) -> Generation:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT {} FROM generations WHERE id = %s").format(GENERATION_COLUMNS),
                (generation_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(generation_id)
        return generation(row)

    async def generations(self, corpus: str | None) -> list[Generation]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT {} FROM generations WHERE state <> 'retired'"
                    " AND (%(corpus)s::text IS NULL OR corpus = %(corpus)s) ORDER BY id"
                ).format(GENERATION_COLUMNS),
                {"corpus": corpus},
            )
            return [generation(row) for row in await cursor.fetchall()]

    async def live(self, corpus: str) -> Generation | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT {} FROM generations WHERE corpus = %s AND state = 'live'").format(
                    GENERATION_COLUMNS
                ),
                (corpus,),
            )
            row = await cursor.fetchone()
        return None if row is None else generation(row)

    async def enqueue(self, generation_id: int, releases: list[tuple[str, str | None]]) -> None:
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO index_jobs (generation, release, name) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(generation_id, release, name) for release, name in releases],
            )

    async def latest_valid_releases(self) -> dict[str, str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT DISTINCT ON (name) name, hash FROM packages"
                " WHERE name IS NOT NULL AND hash NOT IN (SELECT hash FROM invalid_releases)"
                " ORDER BY name, version_order DESC"
            )
            return dict(await cursor.fetchall())

    async def invalid_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT hash FROM invalid_releases")
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def is_invalid(self, release: str) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT 1 FROM invalid_releases WHERE hash = %s", (release,)
            )
            return await cursor.fetchone() is not None

    async def mark_invalid(self, job: Job, base_tag: str, reason: str) -> str | None:
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "INSERT INTO invalid_releases (hash, base_tag, reason) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (job.release, base_tag, reason),
            )
            await connection.execute(
                "UPDATE index_jobs SET state = 'invalid', finished_at = now(), error = %s,"
                " lease_until = NULL WHERE generation = %s AND release = %s",
                (reason, job.generation, job.release),
            )
            cursor = await connection.execute(
                "SELECT p.hash FROM packages p JOIN packages own ON own.hash = %s"
                " WHERE p.name = own.name AND p.version_order < own.version_order"
                " AND p.hash NOT IN (SELECT hash FROM invalid_releases)"
                " ORDER BY p.version_order DESC LIMIT 1",
                (job.release,),
            )
            row = await cursor.fetchone()
        return None if row is None else row[0]

    async def claim(self, lease: timedelta) -> Job | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "UPDATE index_jobs SET state = 'running', attempts = attempts + 1,"
                " lease_until = now() + %s"
                " WHERE (generation, release) = ("
                "   SELECT j.generation, j.release FROM index_jobs j"
                "   JOIN generations g ON g.id = j.generation"
                "   WHERE g.state <> 'retired' AND j.run_after <= now()"
                "   AND (j.state = 'queued' OR (j.state = 'running' AND j.lease_until < now()))"
                "   ORDER BY j.release <> 'base', j.created_at, j.release"
                "   FOR UPDATE OF j SKIP LOCKED LIMIT 1)"
                " RETURNING generation, release, name, attempts",
                (lease,),
            )
            row = await cursor.fetchone()
        return None if row is None else Job(*row)

    async def finish(self, job: Job, state: Literal["done", "superseded", "invalid"]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE index_jobs SET state = %s, finished_at = now(), error = NULL,"
                " lease_until = NULL WHERE generation = %s AND release = %s",
                (state, job.generation, job.release),
            )

    async def fail(self, job: Job, error: str) -> None:
        delay = min(RETRY_BASE * 2 ** (job.attempts - 1), RETRY_LIMIT)
        state = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE index_jobs SET state = %s, error = %s, run_after = now() + %s,"
                " lease_until = NULL WHERE generation = %s AND release = %s",
                (state, error, delay, job.generation, job.release),
            )

    async def retry_failed(self, generation_id: int) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE index_jobs SET state = 'queued', attempts = 0, run_after = now()"
                " WHERE generation = %s AND state = 'failed'",
                (generation_id,),
            )

    async def job_states(self, generation_id: int) -> dict[str, int]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT state, count(*) FROM index_jobs WHERE generation = %s GROUP BY state",
                (generation_id,),
            )
            return dict(await cursor.fetchall())

    async def corpus(self, generation_id: int) -> tuple[list[Definition], list[tuple[str, str]]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT id, kind, name, package, version, module, text, head, first_line, end_line"
                " FROM definitions WHERE generation = %s",
                (generation_id,),
            )
            rows = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT u.id, t.id FROM uses JOIN definitions u ON u.point = uses.user_point"
                " JOIN definitions t ON t.point = uses.target_point WHERE u.generation = %s",
                (generation_id,),
            )
            edges = [(user, target) for user, target in await cursor.fetchall()]
        definitions = [
            Definition(
                id_,
                kind,
                name,
                Origin(None if package is None else Release(package, version), module),
                text,
                head,
                first,
                end,
            )
            for id_, kind, name, package, version, module, text, head, first, end in rows
        ]
        definitions.sort(key=source_order)
        return definitions, sorted(edges)

    async def unfinished(self, generation_id: int) -> int:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT count(*) FROM index_jobs"
                " WHERE generation = %s AND state NOT IN ('done', 'superseded', 'invalid')",
                (generation_id,),
            )
            row = await cursor.fetchone()
        return 0 if row is None else row[0]

    async def promote(self, generation_id: int) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "UPDATE generations SET state = 'retired', retired_at = now()"
                " WHERE corpus = (SELECT corpus FROM generations WHERE id = %s) AND id <> %s"
                " AND (state = 'live' OR (state = 'building' AND id < %s))",
                (generation_id, generation_id, generation_id),
            )
            await connection.execute(
                "UPDATE generations SET state = 'live' WHERE id = %s", (generation_id,)
            )

    async def retired(self, grace: timedelta) -> list[Generation]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT {} FROM generations WHERE state = 'retired' AND retired_at < now() - %s"
                ).format(GENERATION_COLUMNS),
                (grace,),
            )
            return [generation(row) for row in await cursor.fetchall()]

    async def delete_generation(self, generation_id: int) -> None:
        async with self.pool.connection() as connection:
            await connection.execute("DELETE FROM generations WHERE id = %s", (generation_id,))

    async def write(self, generation_id: int, artifacts: Artifacts) -> Written | Superseded:
        async with self.pool.connection() as connection, connection.transaction():
            removed = [artifacts.release]
            if artifacts.package is not None:
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                    (generation_id, artifacts.package.name),
                )
                newer = await newer_release(connection, generation_id, artifacts)
                if newer is not None:
                    return Superseded(newer)
                removed += await older_releases(connection, generation_id, artifacts)
            keys = await delete_releases(connection, generation_id, removed)
            points = await insert(connection, generation_id, artifacts)
        new_keys = {indexed.key for indexed in artifacts.definitions}
        targets = {key for indexed in artifacts.definitions for key in indexed.external_targets}
        return Written(points, removed, sorted(keys | new_keys | targets))

    async def release_size(self, generation_id: int, release: str) -> int:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT count(*) FROM definitions WHERE generation = %s AND release = %s",
                (generation_id, release),
            )
            row = await cursor.fetchone()
        return 0 if row is None else row[0]

    async def refresh_external(self, generation_id: int, keys: list[str]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE definitions d SET external = EXISTS ("
                "  SELECT 1 FROM external_uses e"
                "  WHERE e.generation = d.generation AND e.target_key = d.key)"
                " WHERE d.generation = %s AND d.key = ANY(%s)",
                (generation_id, keys),
            )


async def newer_release(
    connection: AsyncConnection, generation_id: int, artifacts: Artifacts
) -> str | None:
    cursor = await connection.execute(
        "SELECT d.release FROM definitions d JOIN packages p ON p.hash = d.release"
        " JOIN packages own ON own.hash = %s"
        " WHERE d.generation = %s AND d.package = own.name AND d.release <> own.hash"
        " AND p.version_order > own.version_order LIMIT 1",
        (artifacts.release, generation_id),
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]


async def older_releases(
    connection: AsyncConnection, generation_id: int, artifacts: Artifacts
) -> list[str]:
    if artifacts.package is None:
        return []
    cursor = await connection.execute(
        "SELECT DISTINCT release FROM definitions"
        " WHERE generation = %s AND package = %s AND release <> %s",
        (generation_id, artifacts.package.name, artifacts.release),
    )
    return [release for (release,) in await cursor.fetchall()]


async def delete_releases(
    connection: AsyncConnection, generation_id: int, releases: list[str]
) -> set[str]:
    cursor = await connection.execute(
        "SELECT DISTINCT e.target_key FROM external_uses e"
        " JOIN definitions d ON d.point = e.user_point"
        " WHERE d.generation = %s AND d.release = ANY(%s)",
        (generation_id, releases),
    )
    targets = {key for (key,) in await cursor.fetchall()}
    cursor = await connection.execute(
        "DELETE FROM definitions WHERE generation = %s AND release = ANY(%s) RETURNING key",
        (generation_id, releases),
    )
    return targets | {key for (key,) in await cursor.fetchall()}


async def insert(
    connection: AsyncConnection, generation_id: int, artifacts: Artifacts
) -> list[int]:
    count = len(artifacts.definitions)
    cursor = await connection.execute(
        "SELECT nextval(pg_get_serial_sequence('definitions', 'point'))"
        " FROM generate_series(1, %s)",
        (count,),
    )
    points = [point for (point,) in await cursor.fetchall()]
    by_id = {
        indexed.definition.id: point
        for indexed, point in zip(artifacts.definitions, points, strict=True)
    }
    rows = [
        (
            point,
            generation_id,
            artifacts.release,
            indexed.definition.id,
            indexed.key,
            indexed.definition.kind,
            indexed.definition.name,
            None if artifacts.package is None else artifacts.package.name,
            None if artifacts.package is None else artifacts.package.version,
            indexed.definition.origin.module,
            indexed.definition.text,
            indexed.definition.head,
            indexed.definition.start,
            indexed.definition.end,
            digest(indexed.document),
            None if indexed.signature is None else Jsonb(indexed.signature.parameters_json()),
            None if indexed.signature is None else Jsonb(indexed.signature.result_json()),
            indexed.documented,
            indexed.helper,
            indexed.users,
            sorted(by_id[id_] for id_ in indexed.reached),
        )
        for indexed, point in zip(artifacts.definitions, points, strict=True)
    ]
    async with connection.cursor() as many:
        await many.executemany(
            "INSERT INTO definitions (point, generation, release, id, key, kind, name, package,"
            " version, module, text, head, first_line, end_line, document_digest, parameters,"
            " result, documented, helper, users, reached)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s)",
            rows,
        )
        await many.executemany(
            "INSERT INTO uses (user_point, target_point) VALUES (%s, %s)",
            [(by_id[user], by_id[target]) for user, target in artifacts.uses],
        )
        await many.executemany(
            "INSERT INTO external_uses (generation, user_point, target_key) VALUES (%s, %s, %s)",
            [
                (generation_id, by_id[indexed.definition.id], key)
                for indexed in artifacts.definitions
                for key in sorted(indexed.external_targets)
            ],
        )
    return points


@dataclass(frozen=True)
class Projection:
    release: str
    points: list[int]
    digests: list[bytes]


async def projections(pool: Pool, generation_id: int) -> list[Projection]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT release, array_agg(point ORDER BY point),"
            " array_agg(document_digest ORDER BY point)"
            " FROM definitions WHERE generation = %s GROUP BY release ORDER BY release",
            (generation_id,),
        )
        rows = await cursor.fetchall()
    return [Projection(release, list(points), list(digests)) for release, points, digests in rows]
