import asyncio
from collections.abc import Hashable, Iterable
from dataclasses import dataclass

import numpy as np

from jendhub.db.pool import Pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors, instructed, normalized
from jendhub.search.ranking import Model, Signals, feature_matrix
from jendhub.search.signatures import Signature
from jendhub.vectors.index import CODE, NAME, Neighbor, VectorConfig, VectorIndex, VectorName

PRODUCTION = "production"
TASK_INSTRUCTION = (
    "Given a programming task, retrieve the Bend library function or type"
    " that a programmer calls to do it"
)
DEPTH = 50
POOL = 1000
CONCURRENCY = 8


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
    length: int
    signature: Signature | None
    documented: bool
    helper: bool
    users: int
    external: bool
    reached: list[int]
    parent: int | None
    children: list[int]

    @property
    def related(self) -> list[int]:
        return self.children if self.parent is None else [self.parent, *self.children]


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


def hit(candidate: Candidate, score: float) -> Hit:
    return Hit(
        candidate.id,
        candidate.kind,
        candidate.name,
        candidate.package,
        candidate.version,
        candidate.module,
        candidate.head,
        score,
    )


def best_by(neighbors: list[Neighbor], key: Iterable[Hashable]) -> dict[Hashable, float]:
    best: dict[Hashable, float] = {}
    for neighbor, found in zip(neighbors, key, strict=True):
        best[found] = max(best.get(found, neighbor.score), neighbor.score)
    return best


def module_key(neighbor: Neighbor) -> Hashable:
    return (neighbor.release, neighbor.module)


class SearchEngine:
    def __init__(
        self,
        pool: Pool,
        vectors: VectorIndex,
        embedder: Embedder,
        model: Model,
        target: Target,
    ) -> None:
        self.pool = pool
        self.vectors = vectors
        self.embedder = embedder
        self.model = model
        self.target = target

    async def search(self, queries: list[str], limit: int) -> list[list[Hit]]:
        texts = [instructed(TASK_INSTRUCTION, query) for query in queries]
        vectors = normalized(await self.embedder.embed(texts))
        slots = asyncio.Semaphore(CONCURRENCY)

        async def bounded(query: str, vector: Vectors) -> list[Hit]:
            async with slots:
                return await self.search_one(query, vector, limit)

        return list(
            await asyncio.gather(
                *(bounded(query, vector) for query, vector in zip(queries, vectors, strict=True))
            )
        )

    async def search_one(self, text: str, vector: Vectors, limit: int) -> list[Hit]:
        target = self.target
        neighbors = await self.vectors.search(target.collection, target.config, vector, POOL)
        if not neighbors:
            return []
        similarities = {neighbor.point: neighbor.score for neighbor in neighbors}
        floor = neighbors[-1].score
        modules = best_by(neighbors, map(module_key, neighbors))
        packages = best_by(neighbors, (neighbor.release for neighbor in neighbors))
        top = neighbors[:DEPTH]
        points = [neighbor.point for neighbor in top]
        candidates, names = await asyncio.gather(
            self.candidates(points), self.scores(vector, points, NAME)
        )
        related = {
            point
            for candidate in candidates.values()
            for point in candidate.related
            if point not in similarities
        }
        known = similarities | await self.scores(vector, sorted(related), CODE)

        def reached(candidate: Candidate) -> float | None:
            if not candidate.reached:
                return None
            found = [similarities[point] for point in candidate.reached if point in similarities]
            return max(found) if found else floor

        def signals(neighbor: Neighbor, candidate: Candidate) -> Signals:
            return Signals(
                candidate.name,
                candidate.kind,
                candidate.package is None,
                candidate.length,
                candidate.signature,
                candidate.documented,
                candidate.helper,
                candidate.users,
                candidate.external,
                module_key(neighbor),
                neighbor.score,
                names[neighbor.point],
                reached(candidate),
                modules[module_key(neighbor)],
                packages[neighbor.release],
                None if candidate.parent is None else known.get(candidate.parent),
                [known[point] for point in candidate.children if point in known],
            )

        ranked = [
            (neighbor, candidate)
            for neighbor in top
            if (candidate := candidates.get(neighbor.point)) is not None and neighbor.point in names
        ]
        if not ranked:
            return []
        matrix = feature_matrix(text, [signals(*found) for found in ranked])
        scores = self.model.predict(matrix)
        order: list[int] = np.argsort(-scores, kind="stable")[:limit].tolist()
        return [hit(ranked[index][1], float(scores[index])) for index in order]

    async def scores(
        self, vector: Vectors, points: list[int], using: VectorName
    ) -> dict[int, float]:
        target = self.target
        return await self.vectors.scores(target.collection, target.config, vector, points, using)

    async def candidates(self, points: list[int]) -> dict[int, Candidate]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT point, id, kind, name, package, version, module, head, length(text),"
                " parameters, result, documented, helper, users, external, reached, parent,"
                " children FROM definitions WHERE point = ANY(%s)",
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
                row[8],
                None if row[9] is None else Signature.from_json(row[9], row[10]),
                row[11],
                row[12],
                row[13],
                row[14],
                list(row[15]),
                row[16],
                list(row[17]),
            )
            for row in rows
        }
