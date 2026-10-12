from typing import Protocol

import numpy as np
import numpy.typing as npt

type Vectors = npt.NDArray[np.float32]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> Vectors: ...


def instructed(instruction: str, query: str) -> str:
    return f"Instruct: {instruction}\nQuery:{query}"


def normalized(vectors: Vectors) -> Vectors:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return (vectors / np.where(norms > 0, norms, 1)).astype(np.float32)


class Dense:
    def __init__(
        self, embedder: Embedder, documents: list[tuple[str, str]], instruction: str
    ) -> None:
        self.embedder = embedder
        self.instruction = instruction
        self.ids = [id_ for id_, _ in documents]
        self.positions = {id_: position for position, id_ in enumerate(self.ids)}
        self.vectors = normalized(embedder.embed([text for _, text in documents]))

    def similarities(self, queries: list[str]) -> Vectors:
        texts = [instructed(self.instruction, query) for query in queries]
        return normalized(self.embedder.embed(texts)) @ self.vectors.T

    def search(self, queries: list[str], limit: int) -> list[list[str]]:
        orders = np.argsort(-self.similarities(queries), axis=1, kind="stable")[:, :limit]
        return [[self.ids[document] for document in order] for order in orders]

    def scores(self, queries: list[str], ids: list[list[str]]) -> list[list[float]]:
        similarities = self.similarities(queries)
        return [
            [float(row[self.positions[id_]]) for id_ in query_ids]
            for row, query_ids in zip(similarities, ids, strict=True)
        ]
