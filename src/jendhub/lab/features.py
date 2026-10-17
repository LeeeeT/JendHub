import math
from collections import defaultdict
from collections.abc import Callable, Hashable

import numpy as np
import numpy.typing as npt

from jendhub.corpus.definitions import Definition
from jendhub.embedding.model import Vectors
from jendhub.lab.facts import Facts
from jendhub.search.signatures import query_signature, similarity
from jendhub.search.tokens import tokens
from jendhub.vectors.index import VectorConfig

DEPTH = 50
POOL = 1000
EPSILON = 1e-6
PLURALS = (("ies", "y"), ("es", ""), ("s", ""))
SHORTEST_PLURAL = 5
STOPWORDS = frozenset(
    {
        "a", "all", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does",
        "each", "for", "from", "get", "how", "i", "in", "into", "is", "it", "its", "make", "me",
        "my", "not", "of", "on", "or", "so", "than", "that", "the", "then", "this", "to", "we",
        "what", "which", "with", "you",
    }
)  # fmt: skip
RELATIVE = (
    "name",
    "name_overlap",
    "last_coverage",
    "query_coverage",
    "signature",
)
FEATURES = (
    "code",
    "code_z",
    *(f"{base}{suffix}" for base in RELATIVE for suffix in ("", "_gap", "_z")),
    "reached_gain",
    "unused",
    "log_users",
    "external",
    "documented",
    "helper",
    "type",
    "law",
    "base",
    "last_length",
    "depth",
    "log_length",
    "module_gap",
    "package_gap",
    "module_count",
    "parent_gap",
    "children_gap",
    "log_children",
    "query_words",
    "query_best",
)

type Matrix = npt.NDArray[np.float64]


def singular(token: str) -> str:
    for plural, replacement in PLURALS:
        if len(token) >= SHORTEST_PLURAL and token.endswith(plural):
            return token.removesuffix(plural) + replacement
    return token


def stems(text: str) -> frozenset[str]:
    return frozenset(singular(token) for token in tokens(text))


def content_stems(text: str) -> frozenset[str]:
    return frozenset(singular(token) for token in tokens(text) if token not in STOPWORDS)


def share(found: frozenset[str], total: frozenset[str]) -> float:
    return len(found & total) / len(total) if total else 0.0


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def standardized(values: Matrix) -> Matrix:
    return (values - values.mean()) / (values.std() + EPSILON)


def best_by(
    keys: list[Hashable], indexes: npt.NDArray[np.intp], similarities: Vectors
) -> dict[Hashable, float]:
    best: dict[Hashable, float] = {}
    for index in indexes.tolist():
        key = keys[int(index)]
        best[key] = max(best.get(key, -math.inf), float(similarities[index]))
    return best


class Features:
    def __init__(
        self,
        definitions: list[Definition],
        facts: dict[str, Facts],
        code: Vectors,
        names: Vectors,
        config: VectorConfig,
    ) -> None:
        self.ids = [definition.id for definition in definitions]
        self.config = config
        self.code = config.project(code)
        self.names = config.project(names)
        positions = {id_: index for index, id_ in enumerate(self.ids)}
        self.modules: list[Hashable] = [definition.origin for definition in definitions]
        self.releases: list[Hashable] = [definition.origin.release for definition in definitions]
        self.name_tokens = [frozenset(tokens(definition.name)) for definition in definitions]
        self.name_stems = [stems(definition.name) for definition in definitions]
        self.last_stems = [stems(definition.name.rsplit(".", 1)[-1]) for definition in definitions]
        found = [facts[id_] for id_ in self.ids]
        self.signatures = [fact.signature for fact in found]
        self.reached = [
            [positions[id_] for id_ in fact.reached if id_ in positions] for fact in found
        ]
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
        self.fixed = np.array(
            [
                [
                    float(fact.users == 0),
                    math.log1p(fact.users),
                    float(fact.external),
                    float(fact.documented),
                    float(fact.helper),
                    float(definition.kind == "type"),
                    float(definition.kind == "law"),
                    float(definition.origin.release is None),
                    len(definition.name.rsplit(".", 1)[-1]),
                    definition.name.count("."),
                    math.log1p(len(definition.text)),
                    math.log1p(len(children)),
                ]
                for definition, fact, children in zip(
                    definitions, found, self.children, strict=True
                )
            ],
            dtype=np.float64,
        )

    def matrix(self, text: str, vector: Vectors) -> tuple[list[str], Matrix]:
        query = self.config.project(vector.reshape(1, -1))[0]
        similarities = self.code @ query
        pool = np.argsort(-similarities, kind="stable")[:POOL]
        candidates = pool[:DEPTH]
        own = similarities[candidates].astype(np.float64)
        floor = float(similarities[pool[-1]])
        pooled = set(pool.tolist())
        modules = best_by(self.modules, pool, similarities)
        packages = best_by(self.releases, pool, similarities)
        module_counts: defaultdict[Hashable, int] = defaultdict(int)
        for index in candidates:
            module_counts[self.modules[index]] += 1
        query_tokens = frozenset(tokens(text))
        content = content_stems(text)
        signature = query_signature(text)

        def column(value: Callable[[int], float]) -> Matrix:
            return np.array([value(int(index)) for index in candidates], dtype=np.float64)

        def reached_gain(index: int) -> float:
            if not self.reached[index]:
                return 0.0
            known = [float(similarities[point]) for point in self.reached[index] if point in pooled]
            return (max(known) if known else floor) - float(similarities[index])

        def parent_gap(index: int) -> float:
            parent = self.parents[index]
            return math.nan if parent is None else float(similarities[parent] - similarities[index])

        def children_gap(index: int) -> float:
            children = self.children[index]
            if not children:
                return math.nan
            return float(similarities[children].max() - similarities[index])

        def signature_match(index: int) -> float:
            found = self.signatures[index]
            return 0.0 if signature is None or found is None else similarity(signature, found)

        relative = {
            "name": (self.names[candidates] @ query).astype(np.float64),
            "name_overlap": column(lambda index: jaccard(query_tokens, self.name_tokens[index])),
            "last_coverage": column(lambda index: share(content, self.last_stems[index])),
            "query_coverage": column(lambda index: share(self.name_stems[index], content)),
            "signature": column(signature_match),
        }
        columns = [own, standardized(own)]
        for values in relative.values():
            columns += [values, values - values.max(), standardized(values)]
        columns.append(column(reached_gain))
        columns += list(self.fixed[candidates, :-1].T)
        columns += [
            column(lambda index: modules[self.modules[index]] - float(similarities[index])),
            column(lambda index: packages[self.releases[index]] - float(similarities[index])),
            column(lambda index: module_counts[self.modules[index]]),
            column(parent_gap),
            column(children_gap),
            self.fixed[candidates, -1],
            np.full(len(candidates), len(text.split()), dtype=np.float64),
            np.full(len(candidates), float(similarities[candidates[0]]), dtype=np.float64),
        ]
        return [self.ids[index] for index in candidates], np.column_stack(columns)
