import numpy as np

from jendhub.embedding.embedder import Embedder
from jendhub.embedding.model import Vectors, normalized
from jendhub.lab.pipeline import QueryBatch


class Dense:
    def __init__(self, ids: list[str], vectors: Vectors, instruction: str) -> None:
        self.ids = ids
        self.positions = {id_: position for position, id_ in enumerate(ids)}
        self.vectors = normalized(vectors)
        self.instruction = instruction

    @staticmethod
    async def build(
        embedder: Embedder, documents: list[tuple[str, str]], instruction: str
    ) -> Dense:
        vectors = await embedder.embed([text for _, text in documents])
        return Dense([id_ for id_, _ in documents], vectors, instruction)

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset({self.instruction})

    def similarities(self, batch: QueryBatch) -> Vectors:
        return batch.vectors[self.instruction] @ self.vectors.T

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]:
        orders = np.argsort(-self.similarities(batch), axis=1, kind="stable")[:, :limit]
        return [[self.ids[document] for document in order] for order in orders]

    def scores(self, batch: QueryBatch, ids: list[list[str]]) -> list[list[float]]:
        return [
            [float(row[self.positions[id_]]) for id_ in query_ids]
            for row, query_ids in zip(self.similarities(batch), ids, strict=True)
        ]
