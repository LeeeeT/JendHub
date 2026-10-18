import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from jendhub.bench.judge import Judge, Pair, current_judgments, pair
from jendhub.bench.judgments import Assessment, Judgment, JudgmentStore, assessments
from jendhub.bench.queries import BenchError, Query, check, queries_within
from jendhub.bench.searchers import EngineSearcher, LabSearcher, Searcher
from jendhub.corpus.corpus import Corpus, corpus_definitions
from jendhub.corpus.definitions import Definition
from jendhub.corpus.dev import DevCorpus, dev_corpus_list, open_corpus
from jendhub.db.pool import Pool, open_pool
from jendhub.embedding.cache import CachedEmbedder, EmbeddingCache
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.enrich.store import EnrichmentStore
from jendhub.enrich.windows import corpus_windows
from jendhub.hub.library import Library
from jendhub.hub.store import PackageStore
from jendhub.indexing.schedule import (
    GenerationSpec,
    corpus_name,
    ensure_generation,
    promote_ready,
)
from jendhub.indexing.store import IndexStore
from jendhub.indexing.worker import Worker
from jendhub.lab.facts import load_facts
from jendhub.lab.pipelines import PIPELINES, Resources
from jendhub.remote.openrouter import OpenRouter, open_openrouter
from jendhub.search.engine import SearchEngine, Target
from jendhub.search.ranking import load_model
from jendhub.search.sources import BASE_RELEASE
from jendhub.settings import (
    DatabaseSettings,
    OpenRouterSettings,
    QdrantSettings,
    load_settings,
)
from jendhub.uses.graph import UseGraph
from jendhub.vectors.index import PRODUCTION, VectorIndex, open_vector_index

QUERIES = Path("bench/queries.jsonl")
JUDGE_PROMPT = Path("bench/JUDGE.md")
LIBRARY = "library"
ENGINE = "production"


@dataclass(frozen=True)
class Services:
    pool: Pool
    vectors: VectorIndex
    openrouter: OpenRouter
    embedder: Embedder


@asynccontextmanager
async def open_services() -> AsyncGenerator[Services]:
    async with (
        open_pool(load_settings(DatabaseSettings)) as pool,
        open_vector_index(load_settings(QdrantSettings)) as vectors,
        open_openrouter(load_settings(OpenRouterSettings)) as openrouter,
    ):
        embedder = CachedEmbedder(EmbeddingCache(pool), RemoteEmbedder(openrouter))
        yield Services(pool, vectors, openrouter, embedder)


async def dev_index(services: Services, library: Library, listed: DevCorpus) -> int:
    index = IndexStore(services.pool)
    packages = PackageStore(services.pool)
    corpus = corpus_name(listed.base_tag, listed.packages)
    releases: list[tuple[str, str | None]] = [(BASE_RELEASE, None)]
    releases += [(package.hash, package.release.name) for package in listed.packages]
    spec = GenerationSpec(corpus, listed.base_tag, releases, PRODUCTION)
    generation = await ensure_generation(index, services.vectors, spec)
    worker = Worker(index, packages, library, services.vectors, services.embedder)
    await worker.drain(generation.id)
    await promote_ready(index, corpus)
    return generation.id


@dataclass
class Benchmark:
    services: Services
    corpus: Corpus
    generation: int
    definitions: list[Definition]
    graph: UseGraph
    resources: Resources
    queries: list[Query]
    judge: Judge
    store: JudgmentStore
    judgments: list[Judgment]
    stale: dict[tuple[str, str], Pair]

    def assessments(self) -> list[Assessment]:
        return assessments(self.queries, self.judgments)

    async def engine(self) -> SearchEngine:
        services = self.services
        found = await IndexStore(services.pool).generation(self.generation)
        target = Target(found.id, found.collection, found.config)
        return SearchEngine(
            services.pool, services.vectors, services.embedder, load_model(), target
        )

    async def searcher(self, name: str) -> Searcher:
        if name == ENGINE:
            return EngineSearcher(await self.engine())
        return LabSearcher(await PIPELINES[name](self.resources), self.services.embedder)

    async def judge_pairs(self, pairs: list[tuple[Query, str]]) -> None:
        known = {(judgment.query, judgment.id) for judgment in self.judgments}
        definitions = {definition.id: definition for definition in self.definitions}
        unique = {(query.text, id_): (query, id_) for query, id_ in pairs}
        new = [
            pair(query, definitions[id_], self.graph, definitions)
            for key, (query, id_) in unique.items()
            if key not in known
        ]

        async def record(judged: Pair, grade: int) -> None:
            judgment = Judgment(judged.query.text, judged.definition.id, grade)
            await self.store.store(self.judge.key(judged), judgment)
            self.judgments.append(judgment)
            self.stale.pop((judgment.query, judgment.id), None)

        await self.judge.judge(self.services.openrouter, new, record)

    def require_current(self) -> None:
        if self.stale:
            raise BenchError(
                f"{len(self.stale)} judged pairs have a stale grade: run jendhub bench judge"
            )


def pipeline_names() -> list[str]:
    return sorted([*PIPELINES, ENGINE])


async def read_queries(
    services: Services, library: Library, listed: DevCorpus, ids: set[str], top: int | None
) -> list[Query]:
    try:
        text = await asyncio.to_thread(QUERIES.read_text, encoding="utf-8")
    except FileNotFoundError as error:
        raise BenchError(f"{QUERIES} does not exist") from error
    known = ids
    if top is not None:
        full = await open_corpus(library, PackageStore(services.pool), listed)
        known = {definition.id for definition in corpus_definitions(full)}
    queries, problems = check(text, known)
    if problems:
        lines = "".join(f"\n{QUERIES}: {problem}" for problem in problems)
        raise BenchError(f"{len(problems)} problems in {QUERIES}:{lines}")
    return queries_within(queries, ids)


@asynccontextmanager
async def open_benchmark(data: Path, top: int | None) -> AsyncGenerator[Benchmark]:
    async with open_services() as services:
        library = Library(data / LIBRARY)
        listed = dev_corpus_list()
        selected = listed if top is None else listed.top(top)
        corpus = await open_corpus(library, PackageStore(services.pool), selected)
        generation = await dev_index(services, library, selected)
        definitions, edges = await IndexStore(services.pool).corpus(generation)
        graph = UseGraph.of(edges)
        windows = corpus_windows(corpus)
        enrichments = (await EnrichmentStore(services.pool).load(windows)).enrichments
        ids = {definition.id for definition in definitions}
        queries = await read_queries(services, library, listed, ids, top)
        judge = Judge(await asyncio.to_thread(JUDGE_PROMPT.read_text, encoding="utf-8"))
        store = JudgmentStore(services.pool)
        judgments, stale = current_judgments(await store.load(), queries, definitions, judge, graph)
        resources = Resources(
            definitions,
            services.embedder,
            enrichments,
            await load_facts(services.pool, generation),
            assessments(queries, judgments),
        )

        yield Benchmark(
            services,
            corpus,
            generation,
            definitions,
            graph,
            resources,
            queries,
            judge,
            store,
            judgments,
            stale,
        )
