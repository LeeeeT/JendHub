from jendhub.bm25 import Bm25
from jendhub.definitions import Definition
from jendhub.evaluate import Pipeline, PipelineBuilder


def bm25(definitions: list[Definition]) -> Pipeline:
    return Bm25([(definition.id, definition.text) for definition in definitions])


PIPELINES: dict[str, PipelineBuilder] = {
    "bm25": bm25,
}
