import hashlib

import numpy as np

from jendhub.embedding.cache import CachedEmbedder, EmbeddingCache
from jendhub.embedding.model import DIMENSIONS, Vectors
from jendhub.hub.api import Listing, Package, Release
from jendhub.hub.base import Base
from jendhub.hub.store import PackageStore
from jendhub.indexing.schedule import schedule_live, wait_until_live
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.indexing.worker import Worker
from jendhub.search.engine import Target
from jendhub.search.tokens import tokens
from jendhub.vectors.index import VectorIndex
from tests.indexing.library_fixture import BASE, TAG

POLL_SECONDS = 0.05
KIT_V1 = "0x" + "1" * 32
KIT_V2 = "0x" + "2" * 32
APP = "0x" + "3" * 32
KIT_SOURCE = "import Base\n\n# Doubles a number.\ndef double(n: Nat) -> Nat:\n  Nat.add(n, n)\n"
APP_SOURCE = (
    "import Base\nimport kit@1.0.0.0/main.bend as K\n\n"
    "def quadruple(n: Nat) -> Nat:\n  K.double(K.double(n))\n"
)


class WordEmbedder:
    async def embed(self, texts: list[str]) -> Vectors:
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in tokens(text.rsplit("Query:", 1)[-1]):
                vectors[row, int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1
        return vectors


async def store_package(store: PackageStore, hash_: str, release: Release, source: str) -> None:
    await store.store_package(Package(Listing(hash_, release), {"main.bend": source.encode()}))


async def index_live(packages: PackageStore, vectors: VectorIndex) -> tuple[IndexStore, Target]:
    index = IndexStore(packages.pool)
    await schedule_live(index, packages, vectors)
    embedder = CachedEmbedder(EmbeddingCache(packages.pool), WordEmbedder())
    worker = Worker(index, packages, vectors, embedder)
    [generation] = await index.generations(LIVE)
    await worker.drain(generation.id)
    await wait_until_live(index, vectors, generation, POLL_SECONDS)
    live = await index.live(LIVE)
    assert live is not None
    return index, Target(live.id, live.collection, live.config)


async def seed(packages: PackageStore) -> None:
    await packages.store_base(Base(TAG, BASE.encode()))
    await store_package(packages, KIT_V1, Release("kit", "1.0.0.0"), KIT_SOURCE)
    await store_package(packages, APP, Release("app", "1.0.0.0"), APP_SOURCE)
