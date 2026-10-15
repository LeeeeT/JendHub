from collections.abc import Callable
from dataclasses import dataclass

from jendhub.api import ReachedMatch, documented, external_use, helper_name, unused, user_count
from jendhub.bm25 import Bm25
from jendhub.definitions import Definition, Origin
from jendhub.dense import Dense, Embedder
from jendhub.enrich import Enrichment
from jendhub.evaluate import Pipeline
from jendhub.fusion import ReciprocalRankFusion
from jendhub.names import NameMatch
from jendhub.rerank import LinearRerank, MaxNormalized, Scorer
from jendhub.roles import ApiRole
from jendhub.signatures import SignatureMatch
from jendhub.uses import UseGraph

NAME_WEIGHT = 1.0
DENSE_NAME_WEIGHT = 0.1
METADATA_WEIGHT = 1.0
HYBRID_TEXT_WEIGHT = 0.4
HYBRID_BM25_WEIGHT = 0.075
HYBRID_ROLE_WEIGHT = 0.075
HYBRID_NAME_WEIGHT = 0.05
GRAPH_NAME_WEIGHT = 0.15
GRAPH_SIGNATURE_WEIGHT = 0.2
GRAPH_REACHED_WEIGHT = 0.15
GRAPH_UNUSED_WEIGHT = 0.075
GRAPH_USER_COUNT_WEIGHT = 0.02
GRAPH_EXTERNAL_WEIGHT = 0.02
GRAPH_DOCUMENTED_WEIGHT = 0.015
GRAPH_HELPER_WEIGHT = -0.04
INSTRUCTION = "Given a programmer's search query, retrieve the Bend definitions that match it"
TASK_INSTRUCTION = (
    "Given a programming task, retrieve the Bend library function or type"
    " that a programmer calls to do it"
)


@dataclass(frozen=True)
class Resources:
    definitions: list[Definition]
    embedder: Embedder
    enrichments: dict[str, Enrichment]
    graph: UseGraph

    def enriched_documents(
        self, view: Callable[[Definition, Enrichment], str]
    ) -> list[tuple[str, str]]:
        return [
            (d.id, d.text if (e := self.enrichments.get(d.id)) is None else view(d, e))
            for d in self.definitions
        ]


type PipelineBuilder = Callable[[Resources], Pipeline]


def origin_words(origin: Origin) -> str:
    return "" if origin.release is None else f"{origin.release.name} {origin.module}"


def origin_line(origin: Origin) -> str:
    return "Base" if origin.release is None else f"{origin.release.name} {origin.module}"


def origin_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(d.id, f"{origin_line(d.origin)}\n{d.text}") for d in definitions]


def text_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(definition.id, definition.text) for definition in definitions]


def head_package_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [
        (definition.id, f"{origin_words(definition.origin)}\n{definition.head}")
        for definition in definitions
    ]


def name_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(definition.id, definition.name) for definition in definitions]


def metadata(enrichment: Enrichment) -> str:
    return "\n".join([enrichment.summary, *enrichment.queries])


def text_summary_documents(resources: Resources) -> list[tuple[str, str]]:
    return resources.enriched_documents(lambda d, e: f"{d.text}\n\n{e.summary}")


def enriched_documents(resources: Resources) -> list[tuple[str, str]]:
    return resources.enriched_documents(lambda d, e: f"{d.text}\n\n{metadata(e)}")


def metadata_documents(resources: Resources) -> list[tuple[str, str]]:
    return resources.enriched_documents(lambda _d, e: metadata(e))


def bm25(resources: Resources) -> Pipeline:
    return Bm25(text_documents(resources.definitions))


def bm25_head_package(resources: Resources) -> Pipeline:
    return Bm25(head_package_documents(resources.definitions))


def bm25_head_package_name_rrf(resources: Resources) -> Pipeline:
    names = NameMatch(name_documents(resources.definitions))
    return ReciprocalRankFusion([bm25_head_package(resources), names])


def bm25_head_package_name_rerank(resources: Resources) -> Pipeline:
    lexical = Bm25(head_package_documents(resources.definitions))
    names = NameMatch(name_documents(resources.definitions))
    return LinearRerank(lexical, [(1.0, MaxNormalized(lexical)), (NAME_WEIGHT, names)])


def dense_text(resources: Resources) -> Pipeline:
    return Dense(resources.embedder, text_documents(resources.definitions), INSTRUCTION)


def dense_head_package(resources: Resources) -> Pipeline:
    return Dense(resources.embedder, head_package_documents(resources.definitions), INSTRUCTION)


def dense_name_rerank(resources: Resources, documents: list[tuple[str, str]]) -> Pipeline:
    dense = Dense(resources.embedder, documents, INSTRUCTION)
    names = NameMatch(name_documents(resources.definitions))
    return LinearRerank(dense, [(1.0, dense), (DENSE_NAME_WEIGHT, names)])


def dense_text_name_rerank(resources: Resources) -> Pipeline:
    return dense_name_rerank(resources, text_documents(resources.definitions))


def dense_text_summary_name_rerank(resources: Resources) -> Pipeline:
    return dense_name_rerank(resources, text_summary_documents(resources))


def dense_enriched_name_rerank(resources: Resources) -> Pipeline:
    return dense_name_rerank(resources, enriched_documents(resources))


def dense_enriched_hybrid_rerank(resources: Resources) -> Pipeline:
    documents = enriched_documents(resources)
    enriched = Dense(resources.embedder, documents, INSTRUCTION)
    text = Dense(resources.embedder, text_documents(resources.definitions), INSTRUCTION)
    features: list[tuple[float, Scorer]] = [
        (1.0, enriched),
        (HYBRID_TEXT_WEIGHT, text),
        (HYBRID_BM25_WEIGHT, MaxNormalized(Bm25(documents))),
        (HYBRID_ROLE_WEIGHT, ApiRole(resources.enrichments)),
        (HYBRID_NAME_WEIGHT, NameMatch(name_documents(resources.definitions))),
    ]
    return LinearRerank(enriched, features)


def dense_text_metadata_name_rerank(resources: Resources) -> Pipeline:
    text = Dense(resources.embedder, text_documents(resources.definitions), INSTRUCTION)
    meta = Dense(resources.embedder, metadata_documents(resources), INSTRUCTION)
    names = NameMatch(name_documents(resources.definitions))
    features = [(1.0, text), (METADATA_WEIGHT, meta), (DENSE_NAME_WEIGHT, names)]
    return LinearRerank(text, list[tuple[float, Scorer]](features))


def dense_origin_graph_rerank(resources: Resources) -> Pipeline:
    definitions = resources.definitions
    graph = resources.graph
    origin = Dense(resources.embedder, origin_documents(definitions), TASK_INSTRUCTION)
    features: list[tuple[float, Scorer]] = [
        (1.0, origin),
        (GRAPH_NAME_WEIGHT, NameMatch(name_documents(definitions))),
        (GRAPH_SIGNATURE_WEIGHT, SignatureMatch(definitions)),
        (GRAPH_REACHED_WEIGHT, ReachedMatch(origin, graph)),
        (GRAPH_UNUSED_WEIGHT, unused(definitions, graph)),
        (GRAPH_USER_COUNT_WEIGHT, user_count(definitions, graph)),
        (GRAPH_EXTERNAL_WEIGHT, external_use(definitions, graph)),
        (GRAPH_DOCUMENTED_WEIGHT, documented(definitions)),
        (GRAPH_HELPER_WEIGHT, helper_name(definitions, graph)),
    ]
    return LinearRerank(origin, features)


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
    "dense-origin+graph+signature+name-rerank": dense_origin_graph_rerank,
}
