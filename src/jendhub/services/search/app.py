import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.memory import MemoryCachedEmbedder
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.indexing.status import GenerationStatus, generation_statuses, invalid_releases
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.remote.openrouter import open_openrouter
from jendhub.search.definitions import read_definition
from jendhub.search.engine import SearchEngine, Target
from jendhub.search.packages import read_package
from jendhub.search.searches import Search, client_of, record_search
from jendhub.search.sources import read_file, read_release_files, usage
from jendhub.services.app import Running, service_app
from jendhub.services.background import every
from jendhub.services.contracts import (
    DefinitionPage,
    FileList,
    FileText,
    PackagePage,
    SearchHit,
    SearchResults,
    StatusPage,
)
from jendhub.services.parameters import (
    MAX_LIMIT,
    PAGE,
    Limit,
    Offset,
    PageLimit,
    QueryText,
    SearchOffset,
)
from jendhub.settings import DatabaseSettings, OpenRouterSettings, QdrantSettings, load_settings
from jendhub.vectors.index import VectorIndex, open_vector_index

logger = logging.getLogger(__name__)


class SearchServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JENDHUB_")

    query_cache_size: int = 10_000
    target_refresh_seconds: float = 10.0
    status_seconds: float = 30.0


@dataclass
class LiveTarget:
    index: IndexStore
    current: Target | None = None

    async def refresh(self) -> None:
        found = await self.index.live(LIVE)
        target = None if found is None else Target(found.id, found.collection, found.config)
        if target != self.current:
            logger.info("searching %s", target)
        self.current = target


@dataclass
class Generations:
    pool: Pool
    lifetime: float
    found: list[GenerationStatus] | None = None
    read_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self) -> list[GenerationStatus]:
        async with self.lock:
            if self.found is None or time.monotonic() - self.read_at > self.lifetime:
                self.found = await generation_statuses(self.pool, LIVE)
                self.read_at = time.monotonic()
            return self.found


@dataclass(frozen=True)
class SearchState:
    pool: Pool
    vectors: VectorIndex
    embedder: Embedder
    live: LiveTarget
    generations: Generations

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


class Caller(BaseModel):
    x_jendhub_client: str | None = None
    x_jendhub_user_agent: str | None = None


NO_CALLER = Caller()


async def keep(pool: Pool, search: Search) -> None:
    try:
        await record_search(pool, search)
    except Exception:
        logger.exception("the search log did not store a search")


def file_routes(running: Running[SearchState]) -> APIRouter:
    router = APIRouter(prefix="/files")

    @router.get("/{release}")
    async def release_files(release: str) -> FileList:
        found = await read_release_files(running.get().pool, release)
        if found is None:
            raise HTTPException(404, f"no release {release}")
        return FileList(**asdict(found))

    @router.get("/{release}/{path:path}")
    async def file(release: str, path: str) -> FileText:
        state = running.get()
        found = await read_file(state.pool, state.target().generation, release, path)
        if found is None:
            raise HTTPException(404, f"no file {release}/{path}")
        return FileText(**asdict(found))

    return router


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
            generations = Generations(pool, settings.status_seconds)
            running.value = SearchState(pool, vectors, embedder, live, generations)
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
        q: QueryText,
        background: BackgroundTasks,
        limit: Limit = 10,
        offset: SearchOffset = 0,
        caller: Annotated[Caller, Header()] = NO_CALLER,
    ) -> SearchResults:
        state = running.get()
        target = state.target()
        engine = SearchEngine(state.pool, state.vectors, state.embedder, target)
        start = time.perf_counter()
        [hits] = await engine.search([q], MAX_LIMIT)
        searched = Search(
            client_of(caller.x_jendhub_client),
            caller.x_jendhub_user_agent,
            q,
            offset,
            limit,
            target.generation,
            [hit.id for hit in hits],
            time.perf_counter() - start,
        )
        background.add_task(keep, state.pool, searched)
        return SearchResults(
            results=[
                SearchHit(
                    **asdict(hit), **asdict(usage(hit.name, hit.package, hit.version, hit.module))
                )
                for hit in hits[offset : offset + limit]
            ],
            total=len(hits),
            offset=offset,
        )

    @app.get("/definitions/{id_:path}")
    async def definition(id_: str) -> DefinitionPage:
        state = running.get()
        found = await read_definition(state.pool, state.target().generation, id_)
        if found is None:
            raise HTTPException(404, f"no definition {id_}")
        return DefinitionPage(
            **asdict(found),
            **asdict(usage(found.name, found.package, found.version, found.module)),
        )

    @app.get("/packages/{name}")
    async def package(
        name: str,
        definitions_offset: Offset = 0,
        definitions_limit: PageLimit = PAGE,
    ) -> PackagePage:
        state = running.get()
        found = await read_package(
            state.pool, state.target().generation, name, definitions_offset, definitions_limit
        )
        if found is None:
            raise HTTPException(404, f"no package {name}")
        return PackagePage(**asdict(found), definitions_offset=definitions_offset)

    @app.get("/status")
    async def status(
        invalid_releases_offset: Offset = 0,
        invalid_releases_limit: PageLimit = PAGE,
    ) -> StatusPage:
        state = running.get()
        found, total = await invalid_releases(
            state.pool, invalid_releases_offset, invalid_releases_limit
        )
        return StatusPage.model_validate(
            {
                "generations": [asdict(status) for status in await state.generations.get()],
                "invalid_releases": [asdict(release) for release in found],
                "invalid_releases_total": total,
                "invalid_releases_offset": invalid_releases_offset,
            }
        )

    app.include_router(file_routes(running))
    return app
