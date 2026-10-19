import math
from collections.abc import Hashable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jendhub.corpus.definitions import Definition
from jendhub.embedding.model import Vectors
from jendhub.lab.facts import Facts
from jendhub.search.ranking import FEATURES, Matrix, Signals, feature_matrix
from jendhub.vectors.index import VectorConfig

DEPTH = 50


def best_by(
    keys: list[Hashable], indexes: npt.NDArray[np.intp], similarities: Vectors
) -> dict[Hashable, float]:
    best: dict[Hashable, float] = {}
    for index in indexes.tolist():
        key = keys[int(index)]
        best[key] = max(best.get(key, -math.inf), float(similarities[index]))
    return best


@dataclass(frozen=True)
class Setup:
    vectors: VectorConfig
    columns: tuple[str, ...]


class Features:
    def __init__(
        self,
        definitions: list[Definition],
        facts: dict[str, Facts],
        code: Vectors,
        names: Vectors,
        setup: Setup,
    ) -> None:
        self.definitions = definitions
        self.columns = [FEATURES.index(column) for column in setup.columns]
        self.ids = [definition.id for definition in definitions]
        self.facts = [facts[id_] for id_ in self.ids]
        self.config = setup.vectors
        self.code = self.config.project(code)
        self.names = self.config.project(names)
        self.modules: list[Hashable] = [definition.origin for definition in definitions]
        self.releases: list[Hashable] = [definition.origin.release for definition in definitions]
        named = {
            (definition.origin, definition.name): index
            for index, definition in enumerate(definitions)
        }
        self.parents = [
            named.get((definition.origin, definition.name.rsplit(".", 1)[0]))
            if "." in definition.name
            else None
            for definition in definitions
        ]
        self.children: list[list[int]] = [[] for _ in definitions]
        for index, parent in enumerate(self.parents):
            if parent is not None:
                self.children[parent].append(index)

    def matrix(self, text: str, vector: Vectors) -> tuple[list[str], Matrix]:
        query = self.config.project(vector.reshape(1, -1))[0]
        similarities = self.code @ query
        nearest = np.argsort(-similarities, kind="stable")[:DEPTH]
        candidates = [int(index) for index in nearest]
        modules = best_by(self.modules, nearest, similarities)
        packages = best_by(self.releases, nearest, similarities)
        names = self.names[candidates] @ query

        def signals(index: int, name_similarity: float) -> Signals:
            definition = self.definitions[index]
            fact = self.facts[index]
            parent = self.parents[index]
            return Signals(
                definition.name,
                definition.kind,
                definition.origin.release is None,
                len(definition.text),
                fact.signature,
                fact.documented,
                fact.helper,
                fact.release_users,
                fact.external,
                self.modules[index],
                float(similarities[index]),
                name_similarity,
                modules[self.modules[index]],
                packages[self.releases[index]],
                None if parent is None else float(similarities[parent]),
                [float(similarities[child]) for child in self.children[index]],
            )

        found = [signals(index, float(name)) for index, name in zip(candidates, names, strict=True)]
        return [self.ids[index] for index in candidates], feature_matrix(text, found)[
            :, self.columns
        ]
