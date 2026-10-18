from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from jendhub.corpus.definitions import Definition
from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors, instructed, normalized
from jendhub.enrich.enricher import Enrichment
from jendhub.lab.bm25 import Bm25
from jendhub.lab.dense import Dense
from jendhub.lab.documents import (
    enriched_documents,
    head_package_documents,
    metadata_documents,
    name_documents,
    text_documents,
    text_summary_documents,
)
from jendhub.lab.facts import Facts
from jendhub.lab.features import Features, Setup
from jendhub.lab.fusion import ReciprocalRankFusion
from jendhub.lab.learned import GradedQuery, LearnedRank, train_folds
from jendhub.lab.names import NameMatch
from jendhub.lab.pipeline import Pipeline, Scorer
from jendhub.lab.rerank import LinearRerank, MaxNormalized
from jendhub.lab.roles import ApiRole
from jendhub.search.documents import name_document, origin_document
from jendhub.search.engine import POOL, TASK_INSTRUCTION
from jendhub.search.ranking import FEATURES, Model, Trees
from jendhub.vectors.index import PRODUCTION

DEPLOYED = Setup(PRODUCTION, POOL, FEATURES)
LEAN_FEATURES = (
    "code",
    "name",
    "signature",
    "unused",
    "log_users",
    "external",
    "documented",
    "helper",
    "type",
    "law",
    "base",
    "last_length",
    "depth",
    "log_length",
    "module_gap",
    "package_gap",
    "module_count",
    "parent_gap",
    "children_gap",
    "log_children",
)
LEAN = Setup(PRODUCTION, 50, LEAN_FEATURES)
NAME_WEIGHT = 1.0
DENSE_NAME_WEIGHT = 0.1
METADATA_WEIGHT = 1.0
HYBRID_TEXT_WEIGHT = 0.4
HYBRID_BM25_WEIGHT = 0.075
HYBRID_ROLE_WEIGHT = 0.075
HYBRID_NAME_WEIGHT = 0.05
INSTRUCTION = "Given a programmer's search query, retrieve the Bend definitions that match it"


@dataclass(frozen=True)
class Resources:
    definitions: list[Definition]
    embedder: Embedder
    enrichments: dict[str, Enrichment]
    facts: dict[str, Facts]
    graded: Sequence[GradedQuery]

    async def dense(self, documents: list[tuple[str, str]]) -> Dense:
        return await Dense.build(self.embedder, documents, INSTRUCTION)

    async def features(self, setup: Setup) -> Features:
        embed = self.embedder.embed
        return Features(
            self.definitions,
            self.facts,
            await embed([origin_document(definition) for definition in self.definitions]),
            await embed([name_document(definition) for definition in self.definitions]),
            setup,
        )

    async def graded_vectors(self) -> Vectors:
        texts = [instructed(TASK_INSTRUCTION, query.text) for query in self.graded]
        return normalized(await self.embedder.embed(texts))


type PipelineBuilder = Callable[[Resources], Awaitable[Pipeline]]


async def bm25(resources: Resources) -> Pipeline:
    return Bm25(text_documents(resources.definitions))


async def bm25_head_package(resources: Resources) -> Pipeline:
    return Bm25(head_package_documents(resources.definitions))


async def bm25_head_package_name_rrf(resources: Resources) -> Pipeline:
    names = NameMatch(name_documents(resources.definitions))
    return ReciprocalRankFusion([await bm25_head_package(resources), names])


async def bm25_head_package_name_rerank(resources: Resources) -> Pipeline:
    lexical = Bm25(head_package_documents(resources.definitions))
    names = NameMatch(name_documents(resources.definitions))
    return LinearRerank(lexical, [(1.0, MaxNormalized(lexical)), (NAME_WEIGHT, names)])


async def dense_text(resources: Resources) -> Pipeline:
    return await resources.dense(text_documents(resources.definitions))


async def dense_head_package(resources: Resources) -> Pipeline:
    return await resources.dense(head_package_documents(resources.definitions))


async def dense_name_rerank(resources: Resources, documents: list[tuple[str, str]]) -> Pipeline:
    dense = await resources.dense(documents)
    names = NameMatch(name_documents(resources.definitions))
    return LinearRerank(dense, [(1.0, dense), (DENSE_NAME_WEIGHT, names)])


async def dense_text_name_rerank(resources: Resources) -> Pipeline:
    return await dense_name_rerank(resources, text_documents(resources.definitions))


async def dense_text_summary_name_rerank(resources: Resources) -> Pipeline:
    return await dense_name_rerank(
        resources, text_summary_documents(resources.definitions, resources.enrichments)
    )


async def dense_enriched_name_rerank(resources: Resources) -> Pipeline:
    return await dense_name_rerank(
        resources, enriched_documents(resources.definitions, resources.enrichments)
    )


async def dense_enriched_hybrid_rerank(resources: Resources) -> Pipeline:
    documents = enriched_documents(resources.definitions, resources.enrichments)
    enriched = await resources.dense(documents)
    text = await resources.dense(text_documents(resources.definitions))
    features: list[tuple[float, Scorer]] = [
        (1.0, enriched),
        (HYBRID_TEXT_WEIGHT, text),
        (HYBRID_BM25_WEIGHT, MaxNormalized(Bm25(documents))),
        (HYBRID_ROLE_WEIGHT, ApiRole(resources.enrichments)),
        (HYBRID_NAME_WEIGHT, NameMatch(name_documents(resources.definitions))),
    ]
    return LinearRerank(enriched, features)


async def dense_text_metadata_name_rerank(resources: Resources) -> Pipeline:
    text = await resources.dense(text_documents(resources.definitions))
    meta = await resources.dense(metadata_documents(resources.definitions, resources.enrichments))
    names = NameMatch(name_documents(resources.definitions))
    features: list[tuple[float, Scorer]] = [
        (1.0, text),
        (METADATA_WEIGHT, meta),
        (DENSE_NAME_WEIGHT, names),
    ]
    return LinearRerank(text, features)


async def learned_rank(resources: Resources, setup: Setup) -> Pipeline:
    features = await resources.features(setup)
    boosters = train_folds(features, resources.graded, await resources.graded_vectors())
    models: list[Model] = [Trees(booster) for booster in boosters]
    return LearnedRank(features, models, TASK_INSTRUCTION)


async def dense_origin_name_lambdamart(resources: Resources) -> Pipeline:
    return await learned_rank(resources, DEPLOYED)


async def dense_origin_name_lambdamart_lean(resources: Resources) -> Pipeline:
    return await learned_rank(resources, LEAN)


PIPELINES: dict[str, PipelineBuilder] = {
    "bm25": bm25,
    "bm25-head-package": bm25_head_package,
    "bm25-head-package+name-rrf": bm25_head_package_name_rrf,
    "bm25-head-package+name-rerank": bm25_head_package_name_rerank,
    "dense-text": dense_text,
    "dense-head-package": dense_head_package,
    "dense-text+name-rerank": dense_text_name_rerank,
    "dense-text-summary+name-rerank": dense_text_summary_name_rerank,
    "dense-enriched+name-rerank": dense_enriched_name_rerank,
    "dense-text+metadata+name-rerank": dense_text_metadata_name_rerank,
    "dense-enriched+text+bm25-enriched+role+name-rerank": dense_enriched_hybrid_rerank,
    "dense-origin+name+lambdamart": dense_origin_name_lambdamart,
    "dense-origin+name+lambdamart-lean": dense_origin_name_lambdamart_lean,
}
