from collections.abc import Callable
from dataclasses import dataclass

from jendhub.bm25 import Bm25
from jendhub.definitions import Definition, Origin
from jendhub.dense import Dense, Embedder
from jendhub.enrich import Enrichment
from jendhub.errors import JendHubError
from jendhub.evaluate import Pipeline
from jendhub.fusion import ReciprocalRankFusion
from jendhub.names import NameMatch
from jendhub.rerank import LinearRerank, MaxNormalized, Scorer

NAME_WEIGHT = 1.0
DENSE_NAME_WEIGHT = 0.1
METADATA_WEIGHT = 1.0
INSTRUCTION = "Given a programmer's search query, retrieve the Bend definitions that match it"


class PipelineError(JendHubError):
    pass


@dataclass(frozen=True)
class Resources:
    definitions: list[Definition]
    embedder: Embedder
    enrichments: dict[str, Enrichment]

    def enriched(self) -> list[tuple[Definition, Enrichment]]:
        missing = [d.id for d in self.definitions if d.id not in self.enrichments]
        if missing:
            raise PipelineError(
                f"{len(missing)} definitions have no enrichment: run jendhub enrich"
            )
        return [(d, self.enrichments[d.id]) for d in self.definitions]


type PipelineBuilder = Callable[[Resources], Pipeline]


def origin_words(origin: Origin) -> str:
    return "" if origin.release is None else f"{origin.release.name} {origin.module}"


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
    return [(d.id, f"{d.text}\n\n{e.summary}") for d, e in resources.enriched()]


def enriched_documents(resources: Resources) -> list[tuple[str, str]]:
    return [(d.id, f"{d.text}\n\n{metadata(e)}") for d, e in resources.enriched()]


def metadata_documents(resources: Resources) -> list[tuple[str, str]]:
    return [(d.id, metadata(e)) for d, e in resources.enriched()]


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


def dense_text_metadata_name_rerank(resources: Resources) -> Pipeline:
    text = Dense(resources.embedder, text_documents(resources.definitions), INSTRUCTION)
    meta = Dense(resources.embedder, metadata_documents(resources), INSTRUCTION)
    names = NameMatch(name_documents(resources.definitions))
    features = [(1.0, text), (METADATA_WEIGHT, meta), (DENSE_NAME_WEIGHT, names)]
    return LinearRerank(text, list[tuple[float, Scorer]](features))


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
}
