import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from jendhub.bench.judge import Judge, Pair, pair, stale_pairs
from jendhub.bench.queries import (
    Assessment,
    BenchError,
    Judgment,
    Key,
    Query,
    assessments,
    check,
    format_queries,
    queries_within,
    with_judgments,
)
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
from jendhub.hub.store import PackageStore, package_store
from jendhub.indexing.schedule import (
    GenerationSpec,
    corpus_name,
    ensure_generation,
    wait_until_live,
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
ENGINE = "production"


@dataclass(frozen=True)
class Services:
    pool: Pool
    packages: PackageStore
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
        yield Services(pool, package_store(pool), vectors, openrouter, embedder)


async def dev_index(services: Services, listed: DevCorpus) -> int:
    index = IndexStore(services.pool)
    corpus = corpus_name(listed.base_tag, listed.packages)
    releases: list[tuple[str, str | None]] = [(BASE_RELEASE, None)]
    releases += [(package.hash, package.release.name) for package in listed.packages]
    spec = GenerationSpec(corpus, listed.base_tag, releases, PRODUCTION)
    generation = await ensure_generation(index, services.vectors, spec)
    worker = Worker(index, services.packages, services.vectors, services.embedder)
    await worker.drain(generation.id)
    await wait_until_live(index, services.vectors, generation)
    return generation.id


@dataclass
class Benchmark:
    services: Services
    corpus: Corpus
    generation: int
    definitions: list[Definition]
    graph: UseGraph
    resources: Resources
    every_query: list[Query]
    queries: list[Query]
    judge: Judge
    stale: dict[Key, Pair]

    def assessments(self) -> list[Assessment]:
        return assessments(self.queries, self.stale)

    def judgments(self) -> list[Judgment]:
        return [
            judgment
            for query in self.queries
            for judgment in query.judgments
            if (query.text, judgment.id) not in self.stale
        ]

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

    def pairs(self, pairs: list[tuple[Query, str]]) -> list[Pair]:
        definitions = {definition.id: definition for definition in self.definitions}
        unique = {(query.text, id_): (query, id_) for query, id_ in pairs}
        return [
            pair(query, definitions[id_], self.graph, definitions) for query, id_ in unique.values()
        ]

    async def grades(self, pairs: list[tuple[Query, str]]) -> dict[Key, int]:
        grades: dict[Key, int] = {}

        async def record(judged: Pair, grade: int) -> None:
            grades[judged.query.text, judged.definition.id] = grade

        await self.judge.judge(self.services.openrouter, self.pairs(pairs), record)
        return grades

    def unjudged(self, pairs: list[tuple[Query, str]]) -> list[Pair]:
        known = {
            (query.text, id_)
            for query in self.queries
            for id_ in [answer.id for answer in query.answers]
            + [judgment.id for judgment in query.judgments]
            if (query.text, id_) not in self.stale
        }
        return self.pairs([(query, id_) for query, id_ in pairs if (query.text, id_) not in known])

    async def judge_pairs(self, pairs: list[tuple[Query, str]]) -> None:
        found: dict[str, list[Judgment]] = {}

        async def record(judged: Pair, grade: int) -> None:
            query = judged.query.text
            found.setdefault(query, []).append(
                Judgment(judged.definition.id, grade, self.judge.key(judged))
            )
            self.stale.pop((query, judged.definition.id), None)

        try:
            await self.judge.judge(self.services.openrouter, self.unjudged(pairs), record)
        finally:
            await self.save(found)

    async def save(self, found: dict[str, list[Judgment]]) -> None:
        if not found:
            return

        def judged(queries: list[Query]) -> list[Query]:
            return [with_judgments(query, found.get(query.text, [])) for query in queries]

        self.queries = judged(self.queries)
        self.every_query = judged(self.every_query)
        await asyncio.to_thread(write_queries, self.every_query)

    def require_current(self) -> None:
        if self.stale:
            raise BenchError(
                f"{len(self.stale)} judged pairs have a stale grade: run jendhub bench judge"
            )


def pipeline_names() -> list[str]:
    return sorted([*PIPELINES, ENGINE])


def write_queries(queries: list[Query]) -> None:
    written = QUERIES.with_name(QUERIES.name + ".new")
    written.write_text(format_queries(queries), encoding="utf-8")
    written.replace(QUERIES)


async def read_queries(
    services: Services, listed: DevCorpus, ids: set[str], top: int | None
) -> tuple[list[Query], list[Query]]:
    try:
        text = await asyncio.to_thread(QUERIES.read_text, encoding="utf-8")
    except FileNotFoundError as error:
        raise BenchError(f"{QUERIES} does not exist") from error
    known = ids
    if top is not None:
        full = await open_corpus(services.packages, listed)
        known = {definition.id for definition in corpus_definitions(full)}
    queries, problems = check(text, known)
    if problems:
        lines = "".join(f"\n{QUERIES}: {problem}" for problem in problems)
        raise BenchError(f"{len(problems)} problems in {QUERIES}:{lines}")
    return queries, queries_within(queries, ids)


@asynccontextmanager
async def open_benchmark(top: int | None) -> AsyncGenerator[Benchmark]:
    async with open_services() as services:
        listed = dev_corpus_list()
        selected = listed if top is None else listed.top(top)
        corpus = await open_corpus(services.packages, selected)
        generation = await dev_index(services, selected)
        definitions, edges = await IndexStore(services.pool).corpus(generation)
        graph = UseGraph.of(edges)
        windows = corpus_windows(corpus)
        enrichments = (await EnrichmentStore(services.pool).load(windows)).enrichments
        ids = {definition.id for definition in definitions}
        every_query, queries = await read_queries(services, listed, ids, top)
        judge = Judge(await asyncio.to_thread(JUDGE_PROMPT.read_text, encoding="utf-8"))
        stale = stale_pairs(queries, definitions, judge, graph)
        resources = Resources(
            definitions,
            services.embedder,
            enrichments,
            await load_facts(services.pool, generation),
            assessments(queries, stale),
        )

        yield Benchmark(
            services,
            corpus,
            generation,
            definitions,
            graph,
            resources,
            every_query,
            queries,
            judge,
            stale,
        )
