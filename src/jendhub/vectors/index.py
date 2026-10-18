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
HNSW_M = 16
HNSW_EF_CONSTRUCT = 128
OVERSAMPLING = 2.0
UPSERT_BATCH = 64
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

    def search_params(self, ef: int) -> models.SearchParams:
        if self.quantization == "none":
            return models.SearchParams(hnsw_ef=ef)
        rescore = models.QuantizationSearchParams(rescore=True, oversampling=OVERSAMPLING)
        return models.SearchParams(hnsw_ef=ef, quantization=rescore)


PRODUCTION = VectorConfig(2048, "binary")


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
                ),
            },
        )
        await self.client.create_payload_index(
            name, RELEASE, field_schema=models.PayloadSchemaType.KEYWORD
        )

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
                        {RELEASE: release, MODULE: module} for module in found.modules[batch]
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
        self, name: str, config: VectorConfig, vector: Vectors, limit: int
    ) -> list[Neighbor]:
        response = await self.client.query_points(
            name,
            query=config.project(vector.reshape(1, -1))[0].tolist(),
            using=CODE,
            search_params=config.search_params(limit),
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
            query_filter=models.Filter(
                must=[models.HasIdCondition(has_id=list[models.ExtendedPointId](points))]
            ),
            search_params=models.SearchParams(
                exact=True, quantization=models.QuantizationSearchParams(ignore=True)
            ),
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
