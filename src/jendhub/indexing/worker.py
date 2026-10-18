import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from prometheus_client import Counter

from jendhub.embedding.embedder import Embedder
from jendhub.errors import JendHubError
from jendhub.hub.library import Library, ensure_base, refresh
from jendhub.hub.store import PackageStore
from jendhub.indexing.artifacts import build
from jendhub.indexing.failures import LoadError, summary
from jendhub.indexing.store import LIVE, Generation, IndexStore, Job, Superseded
from jendhub.search.sources import BASE_RELEASE
from jendhub.vectors.index import Projected, VectorIndex

LEASE = timedelta(minutes=15)
IDLE_SECONDS = 5.0
CHUNK = 512

JOBS = Counter("jendhub_index_jobs", "The index jobs that a worker finished.", ["outcome"])
MISMATCHES = Counter(
    "jendhub_extraction_mismatches",
    "The files where the extracted definitions differ from the compiler's declarations.",
)

logger = logging.getLogger(__name__)


class IndexingError(JendHubError):
    pass


@dataclass
class Worker:
    index: IndexStore
    packages: PackageStore
    library: Library
    vectors: VectorIndex
    embedder: Embedder
    library_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def run(self, job: Job) -> None:
        generation = await self.index.generation(job.generation)
        if await self.index.is_invalid(job.release):
            await self.index.finish(job, "invalid")
            JOBS.labels("invalid").inc()
            return
        async with self.library_lock:
            await refresh(self.library, self.packages)
            base = await ensure_base(self.library, self.packages, generation.base_tag)
        package = None
        if job.release != BASE_RELEASE:
            package = (await self.packages.releases([job.release]))[job.release]
        artifacts = await build(self.library, base, job.release, package, self.packages.releases)
        if artifacts.errors:
            await self.drop(job, generation, artifacts.errors)
            return
        for label, mismatch in sorted(artifacts.mismatches.items()):
            MISMATCHES.inc()
            logger.warning(
                "%s: the extraction and the compiler disagree: only extracted %s, only declared %s",
                label,
                sorted(mismatch.only_extracted),
                sorted(mismatch.only_declared),
            )
        documents = [definition.document for definition in artifacts.definitions]
        names = [definition.name_document for definition in artifacts.definitions]
        modules = [definition.definition.origin.module for definition in artifacts.definitions]
        for start in range(0, len(documents), CHUNK):
            await self.embedder.embed(documents[start : start + CHUNK])
            await self.embedder.embed(names[start : start + CHUNK])
        written = await self.index.write(generation.id, artifacts)
        if isinstance(written, Superseded):
            logger.info("%s is superseded by %s", job.release, written.by)
            await self.index.finish(job, "superseded")
            JOBS.labels("superseded").inc()
            return
        for release in written.removed:
            await self.vectors.delete_release(generation.collection, release)
        for start in range(0, len(documents), CHUNK):
            chunk = slice(start, start + CHUNK)
            found = Projected(
                written.points[chunk],
                await self.embedder.embed(documents[chunk]),
                await self.embedder.embed(names[chunk]),
                modules[chunk],
            )
            await self.vectors.upsert(generation.collection, generation.config, found, job.release)
        if not await self.index.release_size(generation.id, job.release):
            await self.vectors.delete_release(generation.collection, job.release)
        await self.index.refresh_external(generation.id, written.keys)
        await self.index.finish(job, "done")
        JOBS.labels("done").inc()
        logger.info(
            "indexed %s in generation %d: %d definitions",
            job.release,
            generation.id,
            len(artifacts.definitions),
        )

    async def drop(self, job: Job, generation: Generation, errors: dict[str, LoadError]) -> None:
        older = await self.index.mark_invalid(job, generation.base_tag, errors)
        JOBS.labels("invalid").inc()
        logger.warning("%s is invalid and never indexed: %s", job.release, summary(errors))
        if older is not None and generation.corpus == LIVE:
            await self.index.enqueue(generation.id, [(older, job.name)])
            logger.info("queued %s, the next older release of %s", older, job.name)

    async def step(self) -> bool:
        job = await self.index.claim(LEASE)
        if job is None:
            return False
        try:
            await self.run(job)
        except Exception as error:  # noqa: BLE001
            logger.warning("indexing %s failed: %r", job.release, error)
            await self.index.fail(job, repr(error))
            JOBS.labels("failed").inc()
        return True

    async def serve(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not await self.step():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), IDLE_SECONDS)

    async def drain(self, generation_id: int) -> None:
        while True:
            states = await self.index.job_states(generation_id)
            if not states.get("queued") and not states.get("running"):
                if states.get("failed"):
                    raise IndexingError(
                        f"{states['failed']} jobs of generation {generation_id} failed"
                    )
                return
            if not await self.step():
                await asyncio.sleep(IDLE_SECONDS)
