import math

import numpy as np

from jendhub.definitions import Definition
from jendhub.dense import Dense, Vectors
from jendhub.uses import UseGraph, reached


class Prior:
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values

    def scores(self, _queries: list[str], ids: list[list[str]]) -> list[list[float]]:
        return [[self.values.get(id_, 0.0) for id_ in query_ids] for query_ids in ids]


def unused(definitions: list[Definition], graph: UseGraph) -> Prior:
    return Prior({d.id: float(d.id not in graph.users) for d in definitions})


def user_count(definitions: list[Definition], graph: UseGraph) -> Prior:
    return Prior({d.id: math.log1p(len(graph.users.get(d.id, frozenset()))) for d in definitions})


def external_use(definitions: list[Definition], graph: UseGraph) -> Prior:
    return Prior({d.id: float(d.id in graph.external_users) for d in definitions})


def documented(definitions: list[Definition]) -> Prior:
    return Prior({d.id: float(d.text.startswith("#")) for d in definitions})


def helper_name(definitions: list[Definition], graph: UseGraph) -> Prior:
    by_id = {definition.id: definition for definition in definitions}

    def is_helper(definition: Definition) -> bool:
        return any(
            by_id[user].origin == definition.origin
            and definition.name.startswith(f"{by_id[user].name}.")
            for user in reached(definition.id, graph.users)
        )

    return Prior({d.id: float(is_helper(d)) for d in definitions})


class ReachedMatch:
    def __init__(self, dense: Dense, graph: UseGraph) -> None:
        self.dense = dense
        self.reached = {
            id_: np.array(
                [dense.positions[found] for found in reached(id_, graph.used)], dtype=np.intp
            )
            for id_ in dense.ids
        }

    def gain(self, similarities: Vectors, id_: str) -> float:
        found = self.reached[id_]
        if not found.size:
            return 0.0
        return float(similarities[found].max() - similarities[self.dense.positions[id_]])

    def scores(self, queries: list[str], ids: list[list[str]]) -> list[list[float]]:
        return [
            [self.gain(row, id_) for id_ in query_ids]
            for row, query_ids in zip(self.dense.similarities(queries), ids, strict=True)
        ]
