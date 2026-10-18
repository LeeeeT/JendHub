import numpy as np

from jendhub.embedding.model import Vectors, instructed
from jendhub.lab.dense import Dense
from jendhub.lab.pipeline import QueryBatch, prepare

INSTRUCTION = "Find the definition"
VECTORS = {
    "List.map": [1.0, 0.0, 0.0],
    "List.fold": [0.6, 0.8, 0.0],
    "Nat.add": [0.0, 0.0, 5.0],
    instructed(INSTRUCTION, "map a list"): [2.0, 0.0, 0.0],
    instructed(INSTRUCTION, "add numbers"): [0.0, 0.0, 1.0],
}


class FixedEmbedder:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    async def embed(self, texts: list[str]) -> Vectors:
        self.requests.append(texts)
        return np.array([VECTORS[text] for text in texts], dtype=np.float32)


async def dense(embedder: FixedEmbedder) -> Dense:
    documents = [("map", "List.map"), ("fold", "List.fold"), ("add", "Nat.add")]
    return await Dense.build(embedder, documents, INSTRUCTION)


async def batch(embedder: FixedEmbedder, queries: list[str]) -> QueryBatch:
    return await prepare(queries, frozenset({INSTRUCTION}), embedder)


async def test_search_ranks_by_cosine_similarity() -> None:
    embedder = FixedEmbedder()
    found = (await dense(embedder)).search(await batch(embedder, ["map a list"]), 10)
    assert found == [["map", "fold", "add"]]


async def test_search_ignores_the_vector_length() -> None:
    embedder = FixedEmbedder()
    found = (await dense(embedder)).search(await batch(embedder, ["add numbers"]), 1)
    assert found == [["add"]]


async def test_prepare_embeds_all_queries_in_one_request() -> None:
    embedder = FixedEmbedder()
    found = (await dense(embedder)).search(await batch(embedder, ["map a list", "add numbers"]), 1)
    assert found == [["map"], ["add"]]
    assert embedder.requests[-1] == [
        instructed(INSTRUCTION, "map a list"),
        instructed(INSTRUCTION, "add numbers"),
    ]


async def test_scores_gives_the_cosine_similarity_of_each_id() -> None:
    embedder = FixedEmbedder()
    queries = await batch(embedder, ["map a list", "add numbers"])
    scores = (await dense(embedder)).scores(queries, [["fold", "add", "map"], ["add"]])
    assert np.allclose(scores[0], [0.6, 0.0, 1.0])
    assert np.allclose(scores[1], [1.0])
