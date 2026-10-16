from dataclasses import dataclass
from typing import Protocol

from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors, instructed, normalized


@dataclass(frozen=True)
class QueryBatch:
    texts: list[str]
    vectors: dict[str, Vectors]


class Pipeline(Protocol):
    @property
    def instructions(self) -> frozenset[str]: ...

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]: ...


class Scorer(Protocol):
    @property
    def instructions(self) -> frozenset[str]: ...

    def scores(self, batch: QueryBatch, ids: list[list[str]], /) -> list[list[float]]: ...


async def prepare(texts: list[str], instructions: frozenset[str], embedder: Embedder) -> QueryBatch:
    vectors = {
        instruction: normalized(
            await embedder.embed([instructed(instruction, text) for text in texts])
        )
        for instruction in sorted(instructions)
    }
    return QueryBatch(texts, vectors)
