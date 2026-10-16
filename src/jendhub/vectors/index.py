from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import numpy as np
from qdrant_client import AsyncQdrantClient, models

from jendhub.embedding.model import Vectors, normalized
from jendhub.settings import QdrantSettings

type Quantization = Literal["none", "int8", "binary"]

RELEASE = "release"
HNSW_M = 16
HNSW_EF_CONSTRUCT = 128
OVERSAMPLING = 2.0
UPSERT_BATCH = 256


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
class Neighbors:
    points: list[int]
    scores: list[float]


class VectorIndex:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self.client = client

    async def exists(self, name: str) -> bool:
        return await self.client.collection_exists(name)

    async def create(self, name: str, config: VectorConfig) -> None:
        if await self.exists(name):
            return
        await self.client.create_collection(
            name,
            vectors_config=models.VectorParams(
                size=config.dimensions, distance=models.Distance.COSINE, on_disk=True
            ),
            hnsw_config=models.HnswConfigDiff(m=HNSW_M, ef_construct=HNSW_EF_CONSTRUCT),
            quantization_config=config.quantization_config(),
        )
        await self.client.create_payload_index(
            name, RELEASE, field_schema=models.PayloadSchemaType.KEYWORD
        )

    async def drop(self, name: str) -> None:
        await self.client.delete_collection(name)

    async def upsert(
        self, name: str, config: VectorConfig, points: list[int], vectors: Vectors, release: str
    ) -> None:
        projected = config.project(vectors)
        for start in range(0, len(points), UPSERT_BATCH):
            batch = slice(start, start + UPSERT_BATCH)
            await self.client.upsert(
                name,
                points=models.Batch(
                    ids=list[models.ExtendedPointId](points[batch]),
                    vectors=projected[batch].tolist(),
                    payloads=[{RELEASE: release}] * len(points[batch]),
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
    ) -> Neighbors:
        response = await self.client.query_points(
            name,
            query=config.project(vector.reshape(1, -1))[0].tolist(),
            search_params=config.search_params(limit),
            limit=limit,
            with_payload=False,
        )
        return Neighbors(
            [int(point.id) for point in response.points],
            [point.score for point in response.points],
        )

    async def vectors(self, name: str, points: list[int]) -> dict[int, Vectors]:
        if not points:
            return {}
        records = await self.client.retrieve(
            name, list[models.ExtendedPointId](points), with_vectors=True
        )
        return {
            int(record.id): np.array(record.vector, dtype=np.float32)
            for record in records
            if isinstance(record.vector, list)
        }


@asynccontextmanager
async def open_vector_index(settings: QdrantSettings) -> AsyncGenerator[VectorIndex]:
    key = None if settings.qdrant_api_key is None else settings.qdrant_api_key.get_secret_value()
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=key, check_compatibility=False)
    try:
        yield VectorIndex(client)
    finally:
        await client.close()
