from collections.abc import Callable
from dataclasses import dataclass

from jendhub.bm25 import Bm25
from jendhub.definitions import Definition, Origin
from jendhub.dense import Dense, Embedder
from jendhub.evaluate import Pipeline
from jendhub.fusion import ReciprocalRankFusion
from jendhub.names import NameMatch
from jendhub.rerank import LinearRerank, MaxNormalized

NAME_WEIGHT = 1.0
DENSE_NAME_WEIGHT = 0.1
INSTRUCTION = "Given a programmer's search query, retrieve the Bend definitions that match it"


@dataclass(frozen=True)
class Resources:
    definitions: list[Definition]
    embedder: Embedder


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


def dense_text_name_rerank(resources: Resources) -> Pipeline:
    dense = Dense(resources.embedder, text_documents(resources.definitions), INSTRUCTION)
    names = NameMatch(name_documents(resources.definitions))
    return LinearRerank(dense, [(1.0, dense), (DENSE_NAME_WEIGHT, names)])


PIPELINES: dict[str, PipelineBuilder] = {
    "bm25": bm25,
    "bm25-head-package": bm25_head_package,
    "bm25-head-package+name-rrf": bm25_head_package_name_rrf,
    "bm25-head-package+name-rerank": bm25_head_package_name_rerank,
    "dense-text": dense_text,
    "dense-head-package": dense_head_package,
    "dense-text+name-rerank": dense_text_name_rerank,
}
