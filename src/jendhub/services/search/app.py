import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass

from fastapi import FastAPI, HTTPException, Query
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.memory import MemoryCachedEmbedder
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.remote.openrouter import open_openrouter
from jendhub.search.definitions import read_definition
from jendhub.search.engine import SearchEngine, Target
from jendhub.services.app import Running, service_app
from jendhub.services.background import every
from jendhub.services.contracts import DefinitionPage, SearchHit, SearchResults
from jendhub.settings import DatabaseSettings, OpenRouterSettings, QdrantSettings, load_settings
from jendhub.vectors.index import PRODUCTION, VectorIndex, open_vector_index

MAX_QUERY = 1000
MAX_LIMIT = 50
MAX_PAGE = 500

logger = logging.getLogger(__name__)


class SearchServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    query_cache_size: int = 10_000
    target_refresh_seconds: float = 10.0


@dataclass
class LiveTarget:
    index: IndexStore
    current: Target | None = None

    async def refresh(self) -> None:
        found = await self.index.live_collection(LIVE, PRODUCTION)
        target = None if found is None else Target(found[0], found[1], PRODUCTION)
        if target != self.current:
            logger.info("searching %s", target)
        self.current = target


@dataclass(frozen=True)
class SearchState:
    pool: Pool
    vectors: VectorIndex
    embedder: Embedder
    live: LiveTarget

    def target(self) -> Target:
        if self.live.current is None:
            raise HTTPException(503, "no index is live yet")
        return self.live.current


@dataclass(frozen=True)
class SearchResources:
    pool: Pool
    vectors: VectorIndex
    embedder: Embedder


type OpenResources = Callable[[], AbstractAsyncContextManager[SearchResources]]


@asynccontextmanager
async def open_resources() -> AsyncGenerator[SearchResources]:
    settings = load_settings(SearchServiceSettings)
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
        open_openrouter(load_settings(OpenRouterSettings)) as openrouter,
    ):
        embedder = MemoryCachedEmbedder(RemoteEmbedder(openrouter), settings.query_cache_size)
        yield SearchResources(pool, vectors, embedder)


def create_app(resources: OpenResources = open_resources) -> FastAPI:
    settings = load_settings(SearchServiceSettings)
    running: Running[SearchState] = Running()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with resources() as opened:
            pool, vectors, embedder = opened.pool, opened.vectors, opened.embedder
            live = LiveTarget(IndexStore(pool))
            await live.refresh()
            stop = asyncio.Event()
            follower = asyncio.create_task(
                every(settings.target_refresh_seconds, stop, live.refresh)
            )
            running.value = SearchState(pool, vectors, embedder, live)
            try:
                yield
            finally:
                running.value = None
                stop.set()
                await follower

    async def ready() -> bool:
        state = running.value
        return state is not None and state.live.current is not None

    app = service_app("search", lifespan, ready)

    @app.get("/search")
    async def search(
        q: str = Query(min_length=1, max_length=MAX_QUERY),
        limit: int = Query(10, ge=1, le=MAX_LIMIT),
    ) -> SearchResults:
        state = running.get()
        engine = SearchEngine(state.pool, state.vectors, state.embedder, state.target())
        [hits] = await engine.search([q], limit)
        return SearchResults(results=[SearchHit(**asdict(hit)) for hit in hits])

    @app.get("/definitions/{id_:path}")
    async def definition(
        id_: str,
        used_by_offset: int = Query(0, ge=0),
        used_by_limit: int = Query(100, ge=1, le=MAX_PAGE),
    ) -> DefinitionPage:
        state = running.get()
        found = await read_definition(
            state.pool, state.target().generation, id_, used_by_offset, used_by_limit
        )
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        return DefinitionPage(**asdict(found), used_by_offset=used_by_offset)

    return app
