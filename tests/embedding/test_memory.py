import numpy as np

from jendhub.embedding.memory import MemoryCachedEmbedder
from jendhub.embedding.model import DIMENSIONS, Vectors


class CountingEmbedder:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    async def embed(self, texts: list[str]) -> Vectors:
        self.requests.append(texts)
        return np.ones((len(texts), DIMENSIONS), dtype=np.float32)


async def test_repeated_texts_need_no_request() -> None:
    remote = CountingEmbedder()
    embedder = MemoryCachedEmbedder(remote, 10)
    await embedder.embed(["a", "b"])
    await embedder.embed(["b", "a", "b"])
    assert remote.requests == [["a", "b"]]


async def test_the_oldest_text_leaves_a_full_cache() -> None:
    remote = CountingEmbedder()
    embedder = MemoryCachedEmbedder(remote, 2)
    await embedder.embed(["a"])
    await embedder.embed(["b"])
    await embedder.embed(["c"])
    await embedder.embed(["a"])
    assert remote.requests == [["a"], ["b"], ["c"], ["a"]]


async def test_a_batch_larger_than_the_cache_still_gets_all_vectors() -> None:
    vectors = await MemoryCachedEmbedder(CountingEmbedder(), 1).embed(["a", "b", "c"])
    assert vectors.shape == (3, DIMENSIONS)
