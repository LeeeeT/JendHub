import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from prometheus_client import Counter

from jendhub.embedding.embedder import Embedder
from jendhub.errors import JendHubError
from jendhub.hub.store import PackageStore
from jendhub.indexing.artifacts import build
from jendhub.indexing.failures import LoadError, summary
from jendhub.indexing.store import LIVE, Generation, IndexStore, Job, Superseded
from jendhub.search.sources import BASE_RELEASE
from jendhub.vectors.index import Projected, VectorIndex

LEASE = timedelta(minutes=15)
IDLE_SECONDS = 5.0
CHUNK = 512
CHILD = "jendhub.indexing.child"

JOBS = Counter("jendhub_index_jobs", "The index jobs that a worker finished.", ["outcome"])
MISMATCHES = Counter(
    "jendhub_extraction_mismatches",
    "The files where the extracted definitions differ from the compiler's declarations.",
)

logger = logging.getLogger(__name__)

type Outcome = Literal["done", "superseded", "invalid", "retried", "failed"]


@dataclass(frozen=True)
class Attempt:
    outcome: Outcome
    mismatches: int = 0

    def count(self) -> None:
        JOBS.labels(self.outcome).inc()
        MISMATCHES.inc(self.mismatches)


type Attempter = Callable[[Job], Awaitable[Attempt]]


class IndexingError(JendHubError):
    pass


@dataclass(frozen=True)
class Worker:
    index: IndexStore
    packages: PackageStore
    vectors: VectorIndex
    embedder: Embedder

    async def run(self, job: Job) -> Attempt:
        generation = await self.index.generation(job.generation)
        if await self.index.is_invalid(job.release):
            await self.index.finish(job, "invalid")
            return Attempt("invalid")
        library = self.packages.library
        base = library.base_source(generation.base_tag)
        package = None
        if job.release != BASE_RELEASE:
            package = (await self.packages.releases([job.release]))[job.release]
        artifacts = await build(library, base, job.release, package, self.packages.releases)
        if artifacts.errors:
            await self.drop(job, generation, artifacts.errors)
            return Attempt("invalid")
        for label, mismatch in sorted(artifacts.mismatches.items()):
            logger.warning(
                "%s: the extraction and the compiler disagree: only extracted %s, only declared %s",
                label,
                sorted(mismatch.only_extracted),
                sorted(mismatch.only_declared),
            )
        mismatches = len(artifacts.mismatches)
        documents = [definition.document for definition in artifacts.definitions]
        names = [definition.name_document for definition in artifacts.definitions]
        modules = [definition.definition.origin.module for definition in artifacts.definitions]
        uses = [sorted(definition.used_keys) for definition in artifacts.definitions]
        for start in range(0, len(documents), CHUNK):
            await self.embedder.embed(documents[start : start + CHUNK])
            await self.embedder.embed(names[start : start + CHUNK])
        written = await self.index.write(generation.id, artifacts)
        if isinstance(written, Superseded):
            logger.info("%s is superseded by %s", job.release, written.by)
            await self.index.finish(job, "superseded")
            return Attempt("superseded", mismatches)
        for release in written.removed:
            await self.vectors.delete_release(generation.collection, release)
        for start in range(0, len(documents), CHUNK):
            chunk = slice(start, start + CHUNK)
            found = Projected(
                written.points[chunk],
                await self.embedder.embed(documents[chunk]),
                await self.embedder.embed(names[chunk]),
                modules[chunk],
                uses[chunk],
            )
            await self.vectors.upsert(generation.collection, generation.config, found, job.release)
        if not await self.index.release_size(generation.id, job.release):
            await self.vectors.delete_release(generation.collection, job.release)
        await self.index.finish(job, "done")
        logger.info(
            "indexed %s in generation %d: %d definitions",
            job.release,
            generation.id,
            len(artifacts.definitions),
        )
        return Attempt("done", mismatches)

    async def drop(self, job: Job, generation: Generation, errors: dict[str, LoadError]) -> None:
        older = await self.index.mark_invalid(job, generation.base_tag, errors)
        logger.warning("%s is invalid and never indexed: %s", job.release, summary(errors))
        if older is not None and generation.corpus == LIVE:
            await self.index.enqueue(generation.id, [(older, job.name)])
            logger.info("queued %s, the next older release of %s", older, job.name)

    async def attempt(self, job: Job) -> Attempt:
        try:
            return await self.run(job)
        except Exception as error:  # noqa: BLE001
            return await self.failed(job, repr(error))

    async def failed(self, job: Job, error: str) -> Attempt:
        logger.warning("indexing %s failed: %s", job.release, error)
        return Attempt("failed" if await self.index.fail(job, error) else "retried")

    async def attempt_in_child(self, job: Job) -> Attempt:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            CHILD,
            json.dumps([job.generation, job.release, job.name, job.attempts]),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return await self.failed(
                job, f"its indexing process stopped with exit code {process.returncode}"
            )
        found = json.loads(stdout)
        return Attempt(found["outcome"], found["mismatches"])

    async def step(self, attempter: Attempter | None = None) -> bool:
        for release in await self.index.fail_abandoned():
            JOBS.labels("failed").inc()
            logger.warning("indexing %s failed: its worker stopped during each attempt", release)
        job = await self.index.claim(LEASE)
        if job is None:
            return False
        attempted = await (attempter or self.attempt)(job)
        attempted.count()
        return True

    async def serve(self, stop: asyncio.Event, attempter: Attempter | None = None) -> None:
        while not stop.is_set():
            if not await self.step(attempter):
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
