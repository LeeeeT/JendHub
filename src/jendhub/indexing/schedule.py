import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from jendhub.hub.api import PackageRelease
from jendhub.hub.store import PackageStore
from jendhub.indexing.store import INDEXER_VERSION, LIVE, Generation, IndexStore
from jendhub.search.sources import BASE_RELEASE
from jendhub.vectors.index import PRODUCTION, VectorConfig, VectorIndex

RETIREMENT_GRACE = timedelta(minutes=10)

logger = logging.getLogger(__name__)


def corpus_name(base_tag: str, packages: list[PackageRelease]) -> str:
    listed = "\n".join([base_tag, *sorted(package.hash for package in packages)])
    return f"dev-{hashlib.sha256(listed.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class GenerationSpec:
    corpus: str
    base_tag: str
    releases: list[tuple[str, str | None]]
    config: VectorConfig


def matches(generation: Generation, spec: GenerationSpec) -> bool:
    return (
        generation.base_tag == spec.base_tag
        and generation.indexer_version == INDEXER_VERSION
        and generation.config == spec.config
    )


async def ensure_generation(
    index: IndexStore, vectors: VectorIndex, spec: GenerationSpec
) -> Generation:
    found = [
        generation
        for generation in await index.generations(spec.corpus)
        if matches(generation, spec)
    ]
    if found:
        generation = found[-1]
    else:
        generation = await index.create_generation(spec.corpus, spec.base_tag, spec.config)
        logger.info(
            "created generation %d of %s at %s with %s",
            generation.id,
            spec.corpus,
            spec.base_tag,
            spec.config.label,
        )
    await vectors.create(generation.collection, generation.config)
    await index.enqueue(generation.id, spec.releases)
    if generation.state == "building" and await index.unfinished(generation.id):
        await vectors.pause_indexing(generation.collection)
    return generation


async def promote_ready(index: IndexStore, vectors: VectorIndex, corpus: str) -> Generation | None:
    for generation in reversed(await index.generations(corpus)):
        if generation.state == "live":
            return None
        if await index.unfinished(generation.id):
            continue
        if not await vectors.finish_indexing(generation.collection):
            logger.info("generation %d of %s waits for its vector index", generation.id, corpus)
            return None
        await index.promote(generation.id)
        logger.info("generation %d of %s is live", generation.id, corpus)
        return generation
    return None


async def wait_until_live(
    index: IndexStore, vectors: VectorIndex, generation: Generation, seconds: float = 1.0
) -> None:
    while True:
        await promote_ready(index, vectors, generation.corpus)
        live = await index.live(generation.corpus)
        if live is not None and live.id >= generation.id:
            return
        await asyncio.sleep(seconds)


async def schedule_live(index: IndexStore, packages: PackageStore, vectors: VectorIndex) -> None:
    tag = await packages.latest_base_tag()
    if tag is None:
        return
    latest = await index.latest_valid_releases()
    releases: list[tuple[str, str | None]] = [(BASE_RELEASE, None)]
    releases += [(hash_, name) for name, hash_ in latest.items()]
    await ensure_generation(index, vectors, GenerationSpec(LIVE, tag, releases, PRODUCTION))
    for generation in await index.generations(LIVE):
        await index.enqueue(generation.id, releases)
    await promote_ready(index, vectors, LIVE)


async def clean_retired(index: IndexStore, vectors: VectorIndex) -> None:
    for generation in await index.retired(RETIREMENT_GRACE):
        await vectors.drop(generation.collection)
        await index.delete_generation(generation.id)
        logger.info("deleted retired generation %d", generation.id)
