import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from jendhub.hub.api import PackageRelease
from jendhub.hub.store import PackageStore
from jendhub.indexing.artifacts import BASE_RELEASE
from jendhub.indexing.store import INDEXER_VERSION, LIVE, Collection, Generation, IndexStore
from jendhub.vectors.index import PRODUCTION, VectorConfig, VectorIndex, collection_name

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
    configs: list[VectorConfig]


async def ensure_generation(
    index: IndexStore, vectors: VectorIndex, spec: GenerationSpec
) -> Generation:
    found = [
        generation
        for generation in await index.generations(spec.corpus)
        if generation.base_tag == spec.base_tag and generation.indexer_version == INDEXER_VERSION
    ]
    generation = found[-1] if found else await index.create_generation(spec.corpus, spec.base_tag)
    if not found:
        logger.info("created generation %d of %s at %s", generation.id, spec.corpus, spec.base_tag)
    for config in spec.configs:
        collection = Collection(collection_name(generation.id, config), config)
        await vectors.create(collection.name, config)
        await index.add_collection(generation.id, collection)
    await index.enqueue(generation.id, spec.releases)
    return generation


async def promote_ready(index: IndexStore, corpus: str) -> Generation | None:
    for generation in reversed(await index.generations(corpus)):
        if generation.state == "live":
            return None
        if not await index.unfinished(generation.id):
            await index.promote(generation.id)
            logger.info("generation %d of %s is live", generation.id, corpus)
            return generation
    return None


async def schedule_live(index: IndexStore, packages: PackageStore, vectors: VectorIndex) -> None:
    tag = await packages.latest_base_tag()
    if tag is None:
        return
    latest = await index.latest_valid_releases()
    releases: list[tuple[str, str | None]] = [(BASE_RELEASE, None)]
    releases += [(hash_, name) for name, hash_ in latest.items()]
    await ensure_generation(index, vectors, GenerationSpec(LIVE, tag, releases, [PRODUCTION]))
    for generation in await index.generations(LIVE):
        await index.enqueue(generation.id, releases)
    await promote_ready(index, LIVE)


async def clean_retired(index: IndexStore, vectors: VectorIndex) -> None:
    for generation_id in await index.retired(RETIREMENT_GRACE):
        for collection in await index.collections(generation_id):
            await vectors.drop(collection.name)
        await index.delete_generation(generation_id)
        logger.info("deleted retired generation %d", generation_id)
