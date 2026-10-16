import hashlib
import json
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from jendhub.db.pool import Pool
from jendhub.enrich.enricher import Enrichment, prompt
from jendhub.enrich.windows import Window
from jendhub.errors import JendHubError


class EnrichError(JendHubError):
    pass


@dataclass(frozen=True)
class Stored:
    enrichments: dict[str, Enrichment]
    rejected: list[Window]
    missing: list[Window]


def window_key(window: Window) -> bytes:
    return hashlib.sha256(json.dumps(prompt(window), sort_keys=True).encode()).digest()


class EnrichmentStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def load(self, windows: list[Window]) -> Stored:
        keys = {window_key(window): window for window in windows}
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT window_key, id, summary, queries, role FROM enrichments"
                " WHERE window_key = ANY(%s)",
                (list(keys),),
            )
            rows = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT window_key FROM enrichment_rejections WHERE window_key = ANY(%s)",
                (list(keys),),
            )
            rejected = {key for (key,) in await cursor.fetchall()}
        found: dict[bytes, list[Enrichment]] = {}
        for key, id_, summary, queries, role in rows:
            found.setdefault(key, []).append(Enrichment(id_, summary, queries, role))
        stored = Stored({}, [], [])
        for key, window in keys.items():
            enrichments = found.get(key, [])
            if {e.id for e in enrichments} == {target.id for target in window.targets}:
                stored.enrichments.update((e.id, e) for e in enrichments)
            elif key in rejected:
                stored.rejected.append(window)
            else:
                stored.missing.append(window)
        return stored

    async def store(self, window: Window, enrichments: list[Enrichment]) -> None:
        key = window_key(window)
        rows = [(key, e.id, e.summary, Jsonb(e.queries), e.role) for e in enrichments]
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO enrichments (window_key, id, summary, queries, role)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (window_key, id) DO UPDATE"
                " SET summary = excluded.summary, queries = excluded.queries, role = excluded.role",
                rows,
            )

    async def reject(self, window: Window, reason: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO enrichment_rejections (window_key, reason) VALUES (%s, %s)"
                " ON CONFLICT (window_key) DO UPDATE SET reason = excluded.reason",
                (window_key(window), reason),
            )
