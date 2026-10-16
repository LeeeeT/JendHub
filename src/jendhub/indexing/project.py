import logging

import numpy as np

from jendhub.embedding.cache import EmbeddingCache
from jendhub.errors import JendHubError
from jendhub.indexing.store import Collection, IndexStore, projections
from jendhub.vectors.index import VectorIndex

logger = logging.getLogger(__name__)


class ProjectionError(JendHubError):
    pass


async def project(
    index: IndexStore,
    vectors: VectorIndex,
    cache: EmbeddingCache,
    generation_id: int,
    collection: Collection,
) -> None:
    await vectors.create(collection.name, collection.config)
    for found in await projections(index.pool, generation_id):
        stored = await cache.load(found.digests)
        missing = [digest for digest in found.digests if digest not in stored]
        if missing:
            raise ProjectionError(f"{len(missing)} vectors of {found.release} are not cached")
        matrix = np.stack([stored[digest] for digest in found.digests])
        await vectors.delete_release(collection.name, found.release)
        await vectors.upsert(
            collection.name, collection.config, found.points, matrix, found.release
        )
    await index.add_collection(generation_id, collection)
    logger.info("projected generation %d into %s", generation_id, collection.name)


async def restore_vectors(
    index: IndexStore, vectors: VectorIndex, cache: EmbeddingCache
) -> list[str]:
    restored: list[str] = []
    for generation_id, collection in await index.active_collections():
        if await vectors.exists(collection.name):
            continue
        await project(index, vectors, cache, generation_id, collection)
        restored.append(collection.name)
    return restored
