import numpy as np

from jendhub.db.pool import Pool
from jendhub.embedding.cache import CachedEmbedder, EmbeddingCache
from jendhub.embedding.model import DIMENSIONS, Vectors


class CountingEmbedder:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    async def embed(self, texts: list[str]) -> Vectors:
        self.requests.append(texts)
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            vectors[row, len(text)] = 1.0
        return vectors


async def test_cached_vectors_need_no_request(pool: Pool) -> None:
    remote = CountingEmbedder()
    first = await CachedEmbedder(EmbeddingCache(pool), remote).embed(["ab", "abc"])
    second = await CachedEmbedder(EmbeddingCache(pool), remote).embed(["abc", "ab"])
    assert remote.requests == [["ab", "abc"]]
    assert np.array_equal(first, second[::-1])


async def test_only_missing_texts_are_requested_once(pool: Pool) -> None:
    remote = CountingEmbedder()
    embedder = CachedEmbedder(EmbeddingCache(pool), remote)
    await embedder.embed(["ab"])
    vectors = await embedder.embed(["ab", "abc", "abc"])
    assert remote.requests == [["ab"], ["abc"]]
    assert vectors.shape == (3, DIMENSIONS)


async def test_the_cache_keeps_models_apart(pool: Pool) -> None:
    remote = CountingEmbedder()
    await CachedEmbedder(EmbeddingCache(pool, "a"), remote).embed(["ab"])
    await CachedEmbedder(EmbeddingCache(pool, "b"), remote).embed(["ab"])
    assert remote.requests == [["ab"], ["ab"]]
