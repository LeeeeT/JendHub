import numpy as np

from jendhub.dense import Dense, Vectors, instructed

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

    def embed(self, texts: list[str]) -> Vectors:
        self.requests.append(texts)
        return np.array([VECTORS[text] for text in texts], dtype=np.float32)


def dense(embedder: FixedEmbedder) -> Dense:
    documents = [("map", "List.map"), ("fold", "List.fold"), ("add", "Nat.add")]
    return Dense(embedder, documents, INSTRUCTION)


def test_instructed_puts_the_instruction_before_the_query() -> None:
    assert instructed("Find it", "map") == "Instruct: Find it\nQuery:map"


def test_search_ranks_by_cosine_similarity() -> None:
    assert dense(FixedEmbedder()).search(["map a list"], 10) == [["map", "fold", "add"]]


def test_search_ignores_the_vector_length() -> None:
    assert dense(FixedEmbedder()).search(["add numbers"], 1) == [["add"]]


def test_search_gives_one_ranking_for_each_query() -> None:
    rankings = dense(FixedEmbedder()).search(["map a list", "add numbers"], 1)
    assert rankings == [["map"], ["add"]]


def test_search_embeds_all_queries_in_one_request() -> None:
    embedder = FixedEmbedder()
    dense(embedder).search(["map a list", "add numbers"], 10)
    assert embedder.requests[-1] == [
        instructed(INSTRUCTION, "map a list"),
        instructed(INSTRUCTION, "add numbers"),
    ]


def test_scores_gives_the_cosine_similarity_of_each_id() -> None:
    scores = dense(FixedEmbedder()).scores(
        ["map a list", "add numbers"], [["fold", "add", "map"], ["add"]]
    )
    assert np.allclose(scores[0], [0.6, 0.0, 1.0])
    assert np.allclose(scores[1], [1.0])
