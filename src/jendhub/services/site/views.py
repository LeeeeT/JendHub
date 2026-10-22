import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from fastapi import HTTPException

from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.memory import MemoryCachedEmbedder
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.hub.library import Library
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
from jendhub.search.filters import (
    Filters,
    Resolved,
    UnknownFilterError,
    most_used,
    resolve,
    users_by_key,
)
from jendhub.search.highlights import (
    Extent,
    Highlight,
    LineHighlights,
    read_extents,
    read_file_highlights,
    read_head_highlights,
    read_line_highlights,
    targets,
)
from jendhub.search.packages import PackageDetail, read_package
from jendhub.search.ranking import Model, load_model
from jendhub.search.searches import Caller, Search, record_search
from jendhub.search.sources import (
    ReleaseFiles,
    SourceFile,
    Usage,
    definition_id,
    indexed_file,
    read_file,
    read_release_files,
    usage,
)
from jendhub.services.parameters import MAX_LIMIT
from jendhub.services.site.arguments import (
    DefinitionArguments,
    FilesArguments,
    GrepArguments,
    ReadArguments,
)
from jendhub.services.site.files import (
    NOT_READY,
    Lines,
    Page,
    Place,
    grep_files,
    list_files,
    locate,
    read_lines,
)
from jendhub.settings import (
    DatabaseSettings,
    LibrarySettings,
    OpenRouterSettings,
    QdrantSettings,
    load_settings,
)
from jendhub.vectors.index import VectorIndex, open_vector_index

logger = logging.getLogger(__name__)

EMBEDDING_FAILED = "The embedding service did not answer. Try again."
RIPGREPS = 2
PREVIEW = 5


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
    library: Library
    vectors: VectorIndex
    embedder: Embedder
    model: Model


type OpenResources = Callable[[], AbstractAsyncContextManager[Resources]]


@asynccontextmanager
async def open_resources(query_cache_size: int) -> AsyncGenerator[Resources]:
    library = Library(load_settings(LibrarySettings).library_path)
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
        open_openrouter(load_settings(OpenRouterSettings), INTERACTIVE) as openrouter,
    ):
        embedder = MemoryCachedEmbedder(RemoteEmbedder(openrouter), query_cache_size)
        yield Resources(pool, library, vectors, embedder, load_model())


@dataclass(frozen=True)
class Result:
    hit: Hit
    usage: Usage
    used_by: int


@dataclass(frozen=True)
class Preview:
    ids: list[str]
    total: int


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
    highlights: LineHighlights | None


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
    ripgreps: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(RIPGREPS))

    @property
    def pool(self) -> Pool:
        return self.resources.pool

    @property
    def library(self) -> Library:
        return self.resources.library

    def target(self) -> Target:
        if self.live.current is None:
            raise HTTPException(503, NOT_READY)
        return self.live.current

    async def search(
        self, query: str, filters: Filters, limit: int, offset: int, caller: Caller
    ) -> tuple[Results, Search]:
        query = query.strip()
        if not query and not filters:
            raise HTTPException(400, "Give a query or a filter.")
        target = self.target()
        resources = self.resources
        engine = SearchEngine(
            resources.pool, resources.vectors, resources.embedder, resources.model, target
        )
        start = time.perf_counter()
        hits = await self.found(engine, query, filters)
        searched = Search(
            caller,
            query,
            filters,
            offset,
            limit,
            target.generation,
            [hit.id for hit in hits],
            time.perf_counter() - start,
        )
        page = hits[offset : offset + limit]
        users = await users_by_key(self.pool, target.generation, [hit.key for hit in page])
        shown = [
            Result(
                hit, usage(hit.name, hit.package, hit.version, hit.module), users.get(hit.key, 0)
            )
            for hit in page
        ]
        return Results(query, shown, len(hits), offset), searched

    async def resolve(self, filters: Filters) -> Resolved:
        try:
            return await resolve(self.pool, self.target().generation, filters)
        except UnknownFilterError as unknown:
            raise HTTPException(404, str(unknown)) from unknown

    async def found(self, engine: SearchEngine, query: str, filters: Filters) -> list[Hit]:
        if not query:
            return await most_used(self.pool, await self.resolve(filters), MAX_LIMIT)
        if not filters:
            [hits] = await engine.search([query], MAX_LIMIT)
            return hits
        within = await self.resolve(filters)
        if within.empty:
            return []
        [hits] = await engine.search([query], MAX_LIMIT, within.scope)
        return hits

    async def preview(self, filters: Filters) -> Preview:
        found = await self.resolve(filters)
        hits = await most_used(self.pool, found, PREVIEW)
        sizes = [used.users for used in found.used]
        if found.targets is not None:
            sizes.append(len(found.targets))
        return Preview([hit.id for hit in hits], min(sizes))

    async def keep(self, searched: Search) -> None:
        try:
            await record_search(self.pool, searched)
        except Exception:
            logger.exception("the search log did not store a search")

    async def definition(self, id_: str) -> DefinitionView:
        found = await self.found_definition(id_)
        if found is None:
            raise HTTPException(404, f"The index has no definition {id_}.")
        return found

    async def named(self, given: DefinitionArguments) -> DefinitionView:
        id_ = await definition_id(self.pool, given.path, given.name)
        found = None if id_ is None else await self.found_definition(id_)
        if found is not None:
            return found
        place = await self.locate(given.path)
        if not place.target.is_file():
            raise HTTPException(404, f"The store has no file {place.path}.")
        if await indexed_file(self.pool, self.target().generation, given.path):
            raise HTTPException(
                404,
                f"{place.path} has no definition {given.name}. Give the name as the file"
                " declares it, with no alias of an import line: `parse`, not `Json.parse`.",
            )
        raise HTTPException(
            404,
            f"The index has no definitions of {place.path}. The index holds only Base and the"
            " latest valid release of each package. `read`, `grep` and `files` work on every"
            " release.",
        )

    async def found_definition(self, id_: str) -> DefinitionView | None:
        found = await read_definition(self.pool, self.library, self.target().generation, id_)
        if found is None:
            return None
        return DefinitionView(found, usage(found.name, found.package, found.version, found.module))

    async def locate(self, path: str) -> Place:
        live = self.live.current
        return await locate(
            self.pool, self.library, None if live is None else live.generation, path
        )

    async def read(self, given: ReadArguments) -> Lines:
        return await read_lines(await self.locate(given.path), given.start_line, given.end_line)

    async def grep(self, given: GrepArguments) -> Page:
        place = await self.locate(given.path)
        async with self.ripgreps:
            return await grep_files(place, given)

    async def files_page(self, given: FilesArguments) -> Page:
        place = await self.locate(given.path)
        async with self.ripgreps:
            return await list_files(place, given)

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
        found = await read_release_files(self.pool, self.library, self.target().generation, release)
        if found is None:
            raise HTTPException(404, f"The store has no release {release}.")
        return found

    async def source(self, release: str, path: str) -> SourceFile:
        found = await read_file(self.pool, self.library, self.target().generation, release, path)
        if found is None:
            raise HTTPException(404, f"The store has no file {release}/{path}.")
        return found

    async def file(self, release: str, path: str) -> FileView:
        found = await self.source(release, path)
        async with self.pool.connection() as connection:
            highlights = await read_file_highlights(
                connection, self.target().generation, found.indexed_release, found.module
            )
        return FileView(found, highlights)

    async def extents(self, lines: Iterable[list[Highlight[str]]]) -> dict[str, Extent]:
        async with self.pool.connection() as connection:
            return await read_extents(connection, self.target().generation, targets(lines))

    async def highlights(self, detail: DefinitionDetail) -> dict[int, list[Highlight[str]]]:
        numbers = [line.number for line in detail.imports if line.number is not None]
        numbers += list(range(detail.first_line, detail.end_line))
        async with self.pool.connection() as connection:
            found = await read_line_highlights(
                connection, self.target().generation, detail.release, detail.module, numbers
            )
        return {} if found is None else dict(zip(numbers, found, strict=True))

    async def heads(self, found: Results, lines: int) -> dict[str, LineHighlights]:
        ids = [result.hit.id for result in found.results]
        async with self.pool.connection() as connection:
            return await read_head_highlights(connection, self.target().generation, ids, lines)
