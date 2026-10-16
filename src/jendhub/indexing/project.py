import logging

import numpy as np

from jendhub.embedding.cache import EmbeddingCache
from jendhub.errors import JendHubError
from jendhub.indexing.store import Generation, IndexStore, projections
from jendhub.vectors.index import VectorIndex

logger = logging.getLogger(__name__)


class ProjectionError(JendHubError):
    pass


async def project(
    index: IndexStore, vectors: VectorIndex, cache: EmbeddingCache, generation: Generation
) -> None:
    await vectors.create(generation.collection, generation.config)
    for found in await projections(index.pool, generation.id):
        stored = await cache.load(found.digests)
        missing = [digest for digest in found.digests if digest not in stored]
        if missing:
            raise ProjectionError(f"{len(missing)} vectors of {found.release} are not cached")
        matrix = np.stack([stored[digest] for digest in found.digests])
        await vectors.delete_release(generation.collection, found.release)
        await vectors.upsert(
            generation.collection, generation.config, found.points, matrix, found.release
        )
    logger.info("projected generation %d into %s", generation.id, generation.collection)


async def restore_vectors(
    index: IndexStore, vectors: VectorIndex, cache: EmbeddingCache
) -> list[str]:
    restored: list[str] = []
    for generation in await index.generations(None):
        if await vectors.exists(generation.collection):
            continue
        await project(index, vectors, cache, generation)
        restored.append(generation.collection)
    return restored
