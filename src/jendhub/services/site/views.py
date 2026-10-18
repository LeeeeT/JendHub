import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from fastapi import HTTPException

from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.memory import MemoryCachedEmbedder
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.indexing.status import (
    GenerationStatus,
    InvalidRelease,
    generation_statuses,
    invalid_releases,
)
from jendhub.indexing.store import LIVE, IndexStore
from jendhub.remote.openrouter import INTERACTIVE, open_openrouter
from jendhub.search.definitions import DefinitionDetail, read_definition
from jendhub.search.engine import Hit, SearchEngine, Target
from jendhub.search.packages import PackageDetail, read_package
from jendhub.search.ranking import Model, load_model
from jendhub.search.searches import Client, Search, record_search
from jendhub.search.sources import (
    Declared,
    ReleaseFiles,
    SourceFile,
    Usage,
    read_declared,
    read_file,
    read_release_files,
    source_id,
    usage,
)
from jendhub.services.parameters import MAX_LIMIT
from jendhub.settings import DatabaseSettings, OpenRouterSettings, QdrantSettings, load_settings
from jendhub.vectors.index import VectorIndex, open_vector_index

logger = logging.getLogger(__name__)

EMBEDDING_FAILED = "The embedding service did not answer. Try again."


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
class Resources:
    pool: Pool
    vectors: VectorIndex
    embedder: Embedder
    model: Model


type OpenResources = Callable[[], AbstractAsyncContextManager[Resources]]


@asynccontextmanager
async def open_resources(query_cache_size: int) -> AsyncGenerator[Resources]:
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
        open_openrouter(load_settings(OpenRouterSettings), INTERACTIVE) as openrouter,
    ):
        embedder = MemoryCachedEmbedder(RemoteEmbedder(openrouter), query_cache_size)
        yield Resources(pool, vectors, embedder, load_model())


@dataclass(frozen=True)
class Result:
    hit: Hit
    usage: Usage


@dataclass(frozen=True)
class Results:
    query: str
    results: list[Result]
    total: int
    offset: int


@dataclass(frozen=True)
class DefinitionView:
    detail: DefinitionDetail
    usage: Usage


@dataclass(frozen=True)
class PackageView:
    package: PackageDetail
    offset: int


@dataclass(frozen=True)
class FileView:
    file: SourceFile
    declared: list[Declared]


@dataclass(frozen=True)
class StatusView:
    generations: list[GenerationStatus]
    invalid: list[InvalidRelease]
    invalid_total: int
    offset: int


@dataclass(frozen=True)
class Site:
    resources: Resources
    live: LiveTarget
    generations: Generations
    origin: str

    @property
    def pool(self) -> Pool:
        return self.resources.pool

    def target(self) -> Target:
        if self.live.current is None:
            raise HTTPException(503, "The search index is not ready. Try again later.")
        return self.live.current

    async def search(
        self, query: str, limit: int, offset: int, client: Client, user_agent: str | None
    ) -> tuple[Results, Search]:
        target = self.target()
        resources = self.resources
        engine = SearchEngine(
            resources.pool, resources.vectors, resources.embedder, resources.model, target
        )
        start = time.perf_counter()
        [hits] = await engine.search([query], MAX_LIMIT)
        searched = Search(
            client,
            user_agent,
            query,
            offset,
            limit,
            target.generation,
            [hit.id for hit in hits],
            time.perf_counter() - start,
        )
        shown = [
            Result(hit, usage(hit.name, hit.package, hit.version, hit.module))
            for hit in hits[offset : offset + limit]
        ]
        return Results(query, shown, len(hits), offset), searched

    async def keep(self, searched: Search) -> None:
        try:
            await record_search(self.pool, searched)
        except Exception:
            logger.exception("the search log did not store a search")

    async def definition(self, id_: str) -> DefinitionView:
        found = await read_definition(self.pool, self.target().generation, id_)
        if found is None:
            raise HTTPException(404, f"The index has no definition {id_}.")
        return DefinitionView(found, usage(found.name, found.package, found.version, found.module))

    async def named(self, release: str, path: str, name: str) -> DefinitionView:
        id_ = await source_id(self.pool, release, path, name)
        if id_ is None:
            raise HTTPException(404, f"The index has no definition {name} in {release}/{path}.")
        return await self.definition(id_)

    async def package(self, name: str, offset: int, limit: int) -> PackageView:
        found = await read_package(self.pool, self.target().generation, name, offset, limit)
        if found is None:
            raise HTTPException(404, f"BendHub has no package {name}.")
        return PackageView(found, offset)

    async def status(self, offset: int, limit: int) -> StatusView:
        found, total = await invalid_releases(self.pool, offset, limit)
        return StatusView(await self.generations.get(), found, total, offset)

    async def overview(self) -> GenerationStatus | None:
        found = [found for found in await self.generations.get() if found.state == "live"]
        return found[0] if found else None

    async def files(self, release: str) -> ReleaseFiles:
        found = await read_release_files(self.pool, self.target().generation, release)
        if found is None:
            raise HTTPException(404, f"The store has no release {release}.")
        return found

    async def source(self, release: str, path: str) -> SourceFile:
        found = await read_file(self.pool, self.target().generation, release, path)
        if found is None:
            raise HTTPException(404, f"The store has no file {release}/{path}.")
        return found

    async def file(self, release: str, path: str) -> FileView:
        found = await self.source(release, path)
        return FileView(found, await read_declared(self.pool, self.target().generation, found))
