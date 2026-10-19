import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict

from jendhub.db.pool import open_pool
from jendhub.embedding.cache import CachedEmbedder, EmbeddingCache
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.hub.store import package_store
from jendhub.indexing.store import IndexStore, Job
from jendhub.indexing.worker import Attempt, Worker
from jendhub.logs import configure_logging
from jendhub.remote.openrouter import open_openrouter
from jendhub.settings import DatabaseSettings, OpenRouterSettings, QdrantSettings, load_settings
from jendhub.vectors.index import open_vector_index


@asynccontextmanager
async def open_worker() -> AsyncGenerator[Worker]:
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
        open_openrouter(load_settings(OpenRouterSettings)) as openrouter,
    ):
        embedder = CachedEmbedder(EmbeddingCache(pool), RemoteEmbedder(openrouter))
        yield Worker(IndexStore(pool), package_store(pool), vectors, embedder)


async def attempt(job: Job) -> Attempt:
    async with open_worker() as worker:
        return await worker.attempt(job)


def main() -> None:
    configure_logging(structured=True)
    generation, release, name, attempts = json.loads(sys.argv[1])
    attempted = asyncio.run(attempt(Job(generation, release, name, attempts)))
    sys.stdout.write(json.dumps(asdict(attempted)) + "\n")


if __name__ == "__main__":
    main()
