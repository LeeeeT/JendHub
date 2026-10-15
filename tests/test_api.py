import math
from typing import ClassVar

import numpy as np

from jendhub.api import (
    ReachedMatch,
    documented,
    external_use,
    helper_name,
    unused,
    user_count,
)
from jendhub.definitions import BASE, SourceFile, extract
from jendhub.dense import Dense, Vectors, instructed
from jendhub.uses import UseGraph

SOURCE = """\
# Inserts a key.
def Map.put(m, k):
  Map.put.go(m, k)

def Map.put.go(m, k):
  Map.ins(m, k)

def Map.ins(m, k):
  m
"""
DEFINITIONS = extract(SourceFile(BASE, SOURCE))
GRAPH = UseGraph(
    {"Map.put.go": frozenset({"Map.put"}), "Map.ins": frozenset({"Map.put.go"})},
    {"Map.put": frozenset({"Map.put.go"}), "Map.put.go": frozenset({"Map.ins"})},
    {"Map.put": frozenset({"lib@1.0.0.0/main:root"})},
    {},
)
IDS = [["Map.put", "Map.put.go", "Map.ins"]]


class TableEmbedder:
    vectors: ClassVar[dict[str, list[float]]] = {
        "put": [1.0, 0.0],
        "go": [0.6, 0.8],
        "ins": [0.0, 1.0],
        instructed("find", "query"): [0.0, 1.0],
    }

    def embed(self, texts: list[str]) -> Vectors:
        return np.array([self.vectors[text] for text in texts], dtype=np.float32)


def test_unused_scores_the_definitions_without_users() -> None:
    assert unused(DEFINITIONS, GRAPH).scores(["q"], IDS) == [[1.0, 0.0, 0.0]]


def test_user_count_grows_with_the_users() -> None:
    assert user_count(DEFINITIONS, GRAPH).scores(["q"], IDS) == [[0.0, math.log(2), math.log(2)]]


def test_external_use_scores_the_definitions_that_other_packages_use() -> None:
    assert external_use(DEFINITIONS, GRAPH).scores(["q"], IDS) == [[1.0, 0.0, 0.0]]


def test_documented_scores_the_definitions_with_a_doc_comment() -> None:
    assert documented(DEFINITIONS).scores(["q"], IDS) == [[1.0, 0.0, 0.0]]


def test_helper_name_scores_a_definition_named_under_a_definition_that_reaches_it() -> None:
    assert helper_name(DEFINITIONS, GRAPH).scores(["q"], IDS) == [[0.0, 1.0, 0.0]]


def test_reached_match_gives_how_much_better_a_reached_definition_matches() -> None:
    documents = [("Map.put", "put"), ("Map.put.go", "go"), ("Map.ins", "ins")]
    dense = Dense(TableEmbedder(), documents, "find")
    scores = ReachedMatch(dense, GRAPH).scores(["query"], IDS)
    assert np.allclose(scores, [[1.0, 0.2, 0.0]])
