import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.indexing.child import open_worker
from jendhub.services.app import service_app
from jendhub.settings import load_settings


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    worker_concurrency: int = 2


def create_app() -> FastAPI:
    settings = load_settings(WorkerSettings)
    started = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with open_worker() as worker:
            stop = asyncio.Event()
            tasks = [
                asyncio.create_task(worker.serve(stop, worker.attempt_in_child))
                for _ in range(settings.worker_concurrency)
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
