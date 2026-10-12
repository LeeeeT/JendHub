from jendhub.bm25 import Bm25
from jendhub.definitions import Definition, Origin
from jendhub.evaluate import Pipeline, PipelineBuilder
from jendhub.fusion import ReciprocalRankFusion
from jendhub.names import NameMatch
from jendhub.rerank import LinearRerank, MaxNormalized

NAME_WEIGHT = 1.0


def origin_words(origin: Origin) -> str:
    return "" if origin.release is None else f"{origin.release.name} {origin.module}"


def bm25(definitions: list[Definition]) -> Pipeline:
    return Bm25([(definition.id, definition.text) for definition in definitions])


def head_package_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [
        (definition.id, f"{origin_words(definition.origin)}\n{definition.head}")
        for definition in definitions
    ]


def bm25_head_package(definitions: list[Definition]) -> Pipeline:
    return Bm25(head_package_documents(definitions))


def name_documents(definitions: list[Definition]) -> list[tuple[str, str]]:
    return [(definition.id, definition.name) for definition in definitions]


def bm25_head_package_name_rrf(definitions: list[Definition]) -> Pipeline:
    names = NameMatch(name_documents(definitions))
    return ReciprocalRankFusion([bm25_head_package(definitions), names])


def bm25_head_package_name_rerank(definitions: list[Definition]) -> Pipeline:
    lexical = Bm25(head_package_documents(definitions))
    names = NameMatch(name_documents(definitions))
    return LinearRerank(lexical, [(1.0, MaxNormalized(lexical)), (NAME_WEIGHT, names)])


PIPELINES: dict[str, PipelineBuilder] = {
    "bm25": bm25,
    "bm25-head-package": bm25_head_package,
    "bm25-head-package+name-rrf": bm25_head_package_name_rrf,
    "bm25-head-package+name-rerank": bm25_head_package_name_rerank,
}
