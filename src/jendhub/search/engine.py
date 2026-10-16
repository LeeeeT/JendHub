import asyncio
import math
from dataclasses import dataclass

import numpy as np

from jendhub.db.pool import Pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors, instructed, normalized
from jendhub.search.names import name_tokens, overlap
from jendhub.search.signatures import Signature, query_signature, similarity
from jendhub.vectors.index import VectorConfig, VectorIndex

PRODUCTION = "dense-origin+graph+signature+name-rerank"
TASK_INSTRUCTION = (
    "Given a programming task, retrieve the Bend library function or type"
    " that a programmer calls to do it"
)
DEPTH = 50
POOL = 1000
CONCURRENCY = 8
NAME_CANDIDATES = 10


@dataclass(frozen=True)
class Weights:
    name: float = 0.15
    signature: float = 0.2
    reached: float = 0.15
    unused: float = 0.075
    user_count: float = 0.02
    external: float = 0.02
    documented: float = 0.015
    helper: float = -0.04


WEIGHTS = Weights()


@dataclass(frozen=True)
class Target:
    generation: int
    collection: str
    config: VectorConfig


@dataclass(frozen=True)
class Candidate:
    point: int
    id: str
    kind: str
    name: str
    package: str | None
    version: str | None
    module: str
    head: str
    signature: Signature | None
    documented: bool
    helper: bool
    users: int
    external: bool
    reached: list[int]


@dataclass(frozen=True)
class Hit:
    id: str
    kind: str
    name: str
    package: str | None
    version: str | None
    module: str
    head: str
    score: float


@dataclass(frozen=True)
class Query:
    text: str
    vector: Vectors
    tokens: set[str]
    signature: Signature | None


def reached_gain(candidate: Candidate, own: float, pool: dict[int, float], floor: float) -> float:
    if not candidate.reached:
        return 0.0
    known = [pool[point] for point in candidate.reached if point in pool]
    return (max(known) if known else floor) - own


def score(
    query: Query, candidate: Candidate, own: float, pool: dict[int, float], floor: float
) -> float:
    signature = (
        0.0
        if query.signature is None or candidate.signature is None
        else similarity(query.signature, candidate.signature)
    )
    return (
        own
        + WEIGHTS.name * overlap(query.tokens, name_tokens(candidate.name))
        + WEIGHTS.signature * signature
        + WEIGHTS.reached * reached_gain(candidate, own, pool, floor)
        + WEIGHTS.unused * float(candidate.users == 0)
        + WEIGHTS.user_count * math.log1p(candidate.users)
        + WEIGHTS.external * float(candidate.external)
        + WEIGHTS.documented * float(candidate.documented)
        + WEIGHTS.helper * float(candidate.helper)
    )


def hit(candidate: Candidate, total: float) -> Hit:
    return Hit(
        candidate.id,
        candidate.kind,
        candidate.name,
        candidate.package,
        candidate.version,
        candidate.module,
        candidate.head,
        total,
    )


class SearchEngine:
    def __init__(
        self,
        pool: Pool,
        vectors: VectorIndex,
        embedder: Embedder,
        target: Target,
    ) -> None:
        self.pool = pool
        self.vectors = vectors
        self.embedder = embedder
        self.target = target

    async def search(self, queries: list[str], limit: int) -> list[list[Hit]]:
        texts = [instructed(TASK_INSTRUCTION, query) for query in queries]
        vectors = normalized(await self.embedder.embed(texts))
        prepared = [
            Query(
                text,
                self.target.config.project(vector.reshape(1, -1))[0],
                name_tokens(text),
                query_signature(text),
            )
            for text, vector in zip(queries, vectors, strict=True)
        ]
        slots = asyncio.Semaphore(CONCURRENCY)

        async def bounded(query: Query) -> list[Hit]:
            async with slots:
                return await self.search_one(query, limit)

        return list(await asyncio.gather(*(bounded(query) for query in prepared)))

    async def search_one(self, query: Query, limit: int) -> list[Hit]:
        depth = max(limit, DEPTH)
        neighbors, named = await asyncio.gather(
            self.vectors.search(
                self.target.collection,
                self.target.config,
                query.vector,
                max(POOL, depth),
            ),
            self.name_candidates(query.text),
        )
        pool = dict(zip(neighbors.points, neighbors.scores, strict=True))
        floor = neighbors.scores[-1] if neighbors.scores else 0.0
        dense = neighbors.points[:depth]
        points = list(dict.fromkeys([*dense, *named]))
        candidates = await self.candidates(points)
        similarities = pool | await self.similarities(
            [point for point in points if point not in pool], query.vector
        )
        found = [
            (candidate, score(query, candidate, similarities[candidate.point], pool, floor))
            for point in points
            if (candidate := candidates.get(point)) is not None and point in similarities
        ]
        found.sort(key=lambda item: -item[1])
        return [hit(candidate, total) for candidate, total in found[:limit]]

    async def similarities(self, points: list[int], vector: Vectors) -> dict[int, float]:
        found = await self.vectors.vectors(self.target.collection, points)
        return {point: float(np.dot(normalized(stored), vector)) for point, stored in found.items()}

    async def name_candidates(self, text: str) -> list[int]:
        name = text.strip()
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT point FROM definitions WHERE generation = %s AND (name = %s OR id = %s)"
                " ORDER BY release = 'base' DESC, external DESC, users DESC, point LIMIT %s",
                (self.target.generation, name, name, NAME_CANDIDATES),
            )
            return [point for (point,) in await cursor.fetchall()]

    async def candidates(self, points: list[int]) -> dict[int, Candidate]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT point, id, kind, name, package, version, module, head, parameters, result,"
                " documented, helper, users, external, reached"
                " FROM definitions WHERE point = ANY(%s)",
                (points,),
            )
            rows = await cursor.fetchall()
        return {
            row[0]: Candidate(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                None if row[8] is None else Signature.from_json(row[8], row[9]),
                row[10],
                row[11],
                row[12],
                row[13],
                list(row[14]),
            )
            for row in rows
        }
