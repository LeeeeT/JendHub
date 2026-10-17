import logging

import numpy as np

from jendhub.embedding.cache import EmbeddingCache
from jendhub.embedding.model import Vectors
from jendhub.errors import JendHubError
from jendhub.indexing.store import Generation, IndexStore, Projection, projections
from jendhub.vectors.index import Projected, VectorIndex

CHUNK = 512

logger = logging.getLogger(__name__)


class ProjectionError(JendHubError):
    pass


async def project(
    vectors: VectorIndex,
    cache: EmbeddingCache,
    generation: Generation,
    found: list[Projection],
) -> None:
    async with vectors.bulk(generation.collection):
        for release in found:
            await project_release(vectors, cache, generation, release)
    logger.info(
        "projected %d releases of generation %d into %s",
        len(found),
        generation.id,
        generation.collection,
    )


async def project_release(
    vectors: VectorIndex, cache: EmbeddingCache, generation: Generation, release: Projection
) -> None:
    await vectors.delete_release(generation.collection, release.release)
    for start in range(0, len(release.points), CHUNK):
        chunk = slice(start, start + CHUNK)
        code = await cached(cache, release.release, release.digests[chunk])
        names = await cached(cache, release.release, release.name_digests[chunk])
        found = Projected(release.points[chunk], code, names, release.modules[chunk])
        await vectors.upsert(generation.collection, generation.config, found, release.release)


async def cached(cache: EmbeddingCache, release: str, digests: list[bytes]) -> Vectors:
    stored = await cache.load(digests)
    missing = [digest for digest in digests if digest not in stored]
    if missing:
        raise ProjectionError(f"{len(missing)} vectors of {release} are not cached")
    return np.stack([stored[digest] for digest in digests])


async def restore_vectors(
    index: IndexStore, vectors: VectorIndex, cache: EmbeddingCache
) -> list[str]:
    restored: list[str] = []
    for generation in await index.generations(None):
        await vectors.create(generation.collection, generation.config)
        found = await projections(index.pool, generation.id)
        incomplete = [
            release
            for release in found
            if await vectors.release_size(generation.collection, release.release)
            != len(release.points)
        ]
        if incomplete:
            await project(vectors, cache, generation, incomplete)
            restored.append(generation.collection)
    return restored
