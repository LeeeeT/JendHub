import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.db.pool import open_pool
from jendhub.embedding.cache import CachedEmbedder, EmbeddingCache
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.hub.store import package_store
from jendhub.indexing.store import IndexStore
from jendhub.indexing.worker import Worker
from jendhub.remote.openrouter import open_openrouter
from jendhub.services.app import service_app
from jendhub.settings import DatabaseSettings, OpenRouterSettings, QdrantSettings, load_settings
from jendhub.vectors.index import open_vector_index


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    worker_concurrency: int = 2


def create_app() -> FastAPI:
    settings = load_settings(WorkerSettings)
    started = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with (
            open_pool(load_settings(DatabaseSettings)) as pool,
            open_vector_index(load_settings(QdrantSettings)) as vectors,
            open_openrouter(load_settings(OpenRouterSettings)) as openrouter,
        ):
            embedder = CachedEmbedder(EmbeddingCache(pool), RemoteEmbedder(openrouter))
            worker = Worker(IndexStore(pool), package_store(pool), vectors, embedder)
            stop = asyncio.Event()
            tasks = [
                asyncio.create_task(worker.serve(stop)) for _ in range(settings.worker_concurrency)
            ]
            started.set()
            try:
                yield
            finally:
                stop.set()
                await asyncio.gather(*tasks)

    async def ready() -> bool:
        return started.is_set()

    return service_app("worker", lifespan, ready)
