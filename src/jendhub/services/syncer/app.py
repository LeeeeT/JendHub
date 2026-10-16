import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import Counter, Gauge
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.db.pool import open_pool
from jendhub.hub.store import PackageStore
from jendhub.hub.sync import sync
from jendhub.indexing.schedule import clean_retired, schedule_live
from jendhub.indexing.store import IndexStore
from jendhub.services.app import service_app
from jendhub.services.background import every
from jendhub.settings import DatabaseSettings, QdrantSettings, load_settings
from jendhub.vectors.index import open_vector_index

STORED = Counter("jendhub_sync_packages", "The packages that the syncer stored or rejected.")
LAST_CYCLE = Gauge("jendhub_sync_last_cycle_seconds", "The time of the last finished cycle.")

logger = logging.getLogger(__name__)


class SyncerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    sync_interval_seconds: float = 300.0


def create_app() -> FastAPI:
    settings = load_settings(SyncerSettings)
    healthy = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with (
            open_pool(load_settings(DatabaseSettings)) as pool,
            open_vector_index(load_settings(QdrantSettings)) as vectors,
        ):
            packages = PackageStore(pool)
            index = IndexStore(pool)

            async def cycle() -> None:
                plan = await sync(packages)
                STORED.inc(len(plan.entries))
                await schedule_live(index, packages, vectors)
                await clean_retired(index, vectors)
                LAST_CYCLE.set_to_current_time()
                healthy.set()

            stop = asyncio.Event()
            task = asyncio.create_task(every(settings.sync_interval_seconds, stop, cycle))
            try:
                yield
            finally:
                stop.set()
                await task

    async def ready() -> bool:
        return healthy.is_set()

    return service_app("syncer", lifespan, ready)
