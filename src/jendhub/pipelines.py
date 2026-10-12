from jendhub.bm25 import Bm25
from jendhub.definitions import Definition, Origin
from jendhub.evaluate import Pipeline, PipelineBuilder


def origin_words(origin: Origin) -> str:
    return "" if origin.release is None else f"{origin.release.name} {origin.module}"


def bm25(definitions: list[Definition]) -> Pipeline:
    return Bm25([(definition.id, definition.text) for definition in definitions])


def bm25_head_package(definitions: list[Definition]) -> Pipeline:
    return Bm25(
        [
            (definition.id, f"{origin_words(definition.origin)}\n{definition.head}")
            for definition in definitions
        ]
    )


PIPELINES: dict[str, PipelineBuilder] = {
    "bm25": bm25,
    "bm25-head-package": bm25_head_package,
}
