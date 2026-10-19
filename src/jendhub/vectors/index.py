from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from qdrant_client import AsyncQdrantClient, models

from jendhub.embedding.model import Vectors, normalized
from jendhub.settings import QdrantSettings

type Quantization = Literal["none", "int8", "binary"]
type VectorName = Literal["code", "name"]

CODE: VectorName = "code"
NAME: VectorName = "name"
RELEASE = "release"
MODULE = "module"
USES = "uses"
HNSW_M = 16
HNSW_EF_CONSTRUCT = 128
OVERSAMPLING = 2.0
SEARCH_EF = 200
UPSERT_BATCH = 64
INDEXING_THRESHOLD = 10_000
TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class VectorConfig:
    dimensions: int
    quantization: Quantization

    @property
    def label(self) -> str:
        return f"d{self.dimensions}-{self.quantization}"

    def project(self, vectors: Vectors) -> Vectors:
        return normalized(vectors[:, : self.dimensions])

    def quantization_config(self) -> models.QuantizationConfig | None:
        match self.quantization:
            case "none":
                return None
            case "int8":
                return models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(
                        type=models.ScalarType.INT8, always_ram=True
                    )
                )
            case "binary":
                return models.BinaryQuantization(
                    binary=models.BinaryQuantizationConfig(always_ram=True)
                )

    @property
    def rescores(self) -> bool:
        return self.quantization == "binary"

    def search_params(self, ef: int) -> models.SearchParams:
        match self.quantization:
            case "none":
                return models.SearchParams(hnsw_ef=ef)
            case "int8":
                quantized = models.QuantizationSearchParams(rescore=False)
                return models.SearchParams(hnsw_ef=ef, quantization=quantized)
            case "binary":
                rescore = models.QuantizationSearchParams(rescore=True, oversampling=OVERSAMPLING)
                return models.SearchParams(hnsw_ef=ef, quantization=rescore)

    def exact_params(self) -> models.SearchParams:
        if self.quantization == "none":
            return models.SearchParams(exact=True)
        quantized = models.QuantizationSearchParams(ignore=self.rescores, rescore=False)
        return models.SearchParams(exact=True, quantization=quantized)


PRODUCTION = VectorConfig(2048, "int8")


def collection_name(generation: int, config: VectorConfig) -> str:
    return f"jendhub-g{generation}-{config.label}"


@dataclass(frozen=True)
class Neighbor:
    point: int
    score: float
    release: str
    module: str


@dataclass(frozen=True)
class Projected:
    points: list[int]
    code: Vectors
    names: Vectors
    modules: list[str]
    uses: list[list[str]]


@dataclass(frozen=True)
class Scope:
    release: str | None = None
    uses: tuple[str, ...] = ()
    points: frozenset[int] | None = None

    def query_filter(self) -> models.Filter:
        conditions: list[models.Condition] = [
            models.FieldCondition(key=USES, match=models.MatchValue(value=key)) for key in self.uses
        ]
        if self.release is not None:
            conditions.append(
                models.FieldCondition(key=RELEASE, match=models.MatchValue(value=self.release))
            )
        if self.points is not None:
            conditions.append(
                models.HasIdCondition(has_id=list[models.ExtendedPointId](sorted(self.points)))
            )
        return models.Filter(must=conditions)


def having(points: list[int]) -> models.Filter:
    return models.Filter(must=[models.HasIdCondition(has_id=list[models.ExtendedPointId](points))])


class VectorIndex:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self.client = client

    async def exists(self, name: str) -> bool:
        return await self.client.collection_exists(name)

    async def release_size(self, name: str, release: str) -> int:
        condition = models.FieldCondition(key=RELEASE, match=models.MatchValue(value=release))
        found = await self.client.count(
            name, count_filter=models.Filter(must=[condition]), exact=True
        )
        return found.count

    @asynccontextmanager
    async def bulk(self, name: str) -> AsyncGenerator[None]:
        described = await self.client.get_collection(name)
        threshold = described.config.optimizer_config.indexing_threshold
        await self.client.update_collection(
            name, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0)
        )
        try:
            yield
        finally:
            await self.client.update_collection(
                name, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=threshold)
            )

    async def create(self, name: str, config: VectorConfig) -> None:
        if await self.exists(name):
            return
        await self.client.create_collection(
            name,
            vectors_config={
                CODE: models.VectorParams(
                    size=config.dimensions,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                    hnsw_config=models.HnswConfigDiff(m=HNSW_M, ef_construct=HNSW_EF_CONSTRUCT),
                    quantization_config=config.quantization_config(),
                ),
                NAME: models.VectorParams(
                    size=config.dimensions,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                    hnsw_config=models.HnswConfigDiff(m=0),
                    quantization_config=config.quantization_config(),
                ),
            },
            optimizers_config=models.OptimizersConfigDiff(indexing_threshold=INDEXING_THRESHOLD),
        )
        for field in (RELEASE, USES):
            await self.client.create_payload_index(
                name, field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    async def indexing_threshold(self, name: str) -> int:
        described = await self.client.get_collection(name)
        return described.config.optimizer_config.indexing_threshold or 0

    async def pause_indexing(self, name: str) -> None:
        if await self.indexing_threshold(name):
            await self.client.update_collection(
                name, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0)
            )

    async def finish_indexing(self, name: str) -> bool:
        described = await self.client.get_collection(name)
        if not described.config.optimizer_config.indexing_threshold:
            await self.client.update_collection(
                name,
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=INDEXING_THRESHOLD
                ),
            )
            return False
        return described.status == models.CollectionStatus.GREEN

    async def drop(self, name: str) -> None:
        await self.client.delete_collection(name)

    async def upsert(self, name: str, config: VectorConfig, found: Projected, release: str) -> None:
        code = config.project(found.code)
        names = config.project(found.names)
        for start in range(0, len(found.points), UPSERT_BATCH):
            batch = slice(start, start + UPSERT_BATCH)
            await self.client.upsert(
                name,
                points=models.Batch(
                    ids=list[models.ExtendedPointId](found.points[batch]),
                    vectors={CODE: code[batch].tolist(), NAME: names[batch].tolist()},
                    payloads=[
                        {RELEASE: release, MODULE: module, USES: uses}
                        for module, uses in zip(
                            found.modules[batch], found.uses[batch], strict=True
                        )
                    ],
                ),
                wait=True,
            )

    async def delete_release(self, name: str, release: str) -> None:
        condition = models.FieldCondition(key=RELEASE, match=models.MatchValue(value=release))
        await self.client.delete(
            name,
            points_selector=models.FilterSelector(filter=models.Filter(must=[condition])),
            wait=True,
        )

    async def search(
        self,
        name: str,
        config: VectorConfig,
        vector: Vectors,
        limit: int,
        scope: Scope | None = None,
    ) -> list[Neighbor]:
        response = await self.client.query_points(
            name,
            query=config.project(vector.reshape(1, -1))[0].tolist(),
            using=CODE,
            query_filter=None if scope is None else scope.query_filter(),
            search_params=config.search_params(max(limit, SEARCH_EF)),
            limit=limit,
            with_payload=[RELEASE, MODULE],
        )
        return [
            Neighbor(
                int(point.id),
                point.score,
                str(payload.get(RELEASE)),
                str(payload.get(MODULE)),
            )
            for point in response.points
            if (payload := point.payload) is not None
        ]

    async def scores(
        self,
        name: str,
        config: VectorConfig,
        vector: Vectors,
        points: list[int],
        using: VectorName,
    ) -> dict[int, float]:
        if not points:
            return {}
        response = await self.client.query_points(
            name,
            query=config.project(vector.reshape(1, -1))[0].tolist(),
            using=using,
            query_filter=having(points),
            search_params=config.exact_params(),
            limit=len(points),
            with_payload=False,
        )
        return {int(point.id): point.score for point in response.points}


@asynccontextmanager
async def open_vector_index(settings: QdrantSettings) -> AsyncGenerator[VectorIndex]:
    key = None if settings.qdrant_api_key is None else settings.qdrant_api_key.get_secret_value()
    client = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=key, timeout=TIMEOUT_SECONDS, check_compatibility=False
    )
    try:
        yield VectorIndex(client)
    finally:
        await client.close()
