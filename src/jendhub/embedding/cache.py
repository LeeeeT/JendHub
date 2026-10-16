import numpy as np

from jendhub.db.pool import Pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import CACHE_KEY, DIMENSIONS, Vectors, digest, empty


class EmbeddingCache:
    def __init__(self, pool: Pool, model: str = CACHE_KEY) -> None:
        self.pool = pool
        self.model = model

    async def load(self, digests: list[bytes]) -> dict[bytes, Vectors]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT digest, vector FROM embeddings WHERE model = %s AND digest = ANY(%s)",
                (self.model, list(set(digests))),
            )
            rows = await cursor.fetchall()
        return {key: np.frombuffer(vector, dtype=np.float32) for key, vector in rows}

    async def store(self, digests: list[bytes], vectors: Vectors) -> None:
        rows = [
            (self.model, key, vector.astype(np.float32).tobytes())
            for key, vector in zip(digests, vectors, strict=True)
        ]
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO embeddings (model, digest, vector) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                rows,
            )


class CachedEmbedder:
    def __init__(self, cache: EmbeddingCache, remote: Embedder) -> None:
        self.cache = cache
        self.remote = remote

    async def embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return empty()
        digests = [digest(text) for text in texts]
        found = await self.cache.load(digests)
        missing = list(
            dict.fromkeys(t for t, d in zip(texts, digests, strict=True) if d not in found)
        )
        if missing:
            vectors = await self.remote.embed(missing)
            keys = [digest(text) for text in missing]
            await self.cache.store(keys, vectors)
            found.update(zip(keys, vectors, strict=True))
        return np.stack([found[key] for key in digests]).reshape(-1, DIMENSIONS)
