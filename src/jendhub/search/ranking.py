import math
from collections import Counter
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import lightgbm as lgb
import numpy as np
import numpy.typing as npt

from jendhub.search.signatures import Signature, query_signature, similarity
from jendhub.search.tokens import tokens

MODEL = Path(__file__).with_name("ranker.txt")
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


@dataclass(frozen=True)
class Signals:
    name: str
    kind: str
    base: bool
    length: int
    signature: Signature | None
    documented: bool
    helper: bool
    users: int
    external: bool
    module: Hashable
    code: float
    name_similarity: float
    reached: float | None
    module_best: float
    package_best: float
    parent: float | None
    children: list[float]


class Model(Protocol):
    def predict(self, data: Matrix) -> Matrix: ...


class Trees:
    def __init__(self, booster: lgb.Booster) -> None:
        self.booster = booster

    def predict(self, data: Matrix) -> Matrix:
        scores = self.booster.predict(data)  # pyright: ignore[reportUnknownMemberType]
        return np.asarray(cast("Matrix", scores), dtype=np.float64)


def load_model(path: Path = MODEL) -> Trees:
    return Trees(lgb.Booster(model_file=path))


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


def last_part(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def feature_matrix(text: str, candidates: list[Signals]) -> Matrix:
    query_tokens = frozenset(tokens(text))
    content = content_stems(text)
    signature = query_signature(text)
    modules = Counter(found.module for found in candidates)

    def column(value: Callable[[Signals], float]) -> Matrix:
        return np.array([value(found) for found in candidates], dtype=np.float64)

    def signature_match(found: Signals) -> float:
        if signature is None or found.signature is None:
            return 0.0
        return similarity(signature, found.signature)

    def reached_gain(found: Signals) -> float:
        return 0.0 if found.reached is None else found.reached - found.code

    def parent_gap(found: Signals) -> float:
        return math.nan if found.parent is None else found.parent - found.code

    def children_gap(found: Signals) -> float:
        return max(found.children) - found.code if found.children else math.nan

    code = column(lambda found: found.code)
    relative = [
        column(lambda found: found.name_similarity),
        column(lambda found: jaccard(query_tokens, frozenset(tokens(found.name)))),
        column(lambda found: share(content, stems(last_part(found.name)))),
        column(lambda found: share(stems(found.name), content)),
        column(signature_match),
    ]
    columns = [code, standardized(code)]
    for values in relative:
        columns += [values, values - values.max(), standardized(values)]
    columns += [
        column(reached_gain),
        column(lambda found: float(found.users == 0)),
        column(lambda found: math.log1p(found.users)),
        column(lambda found: float(found.external)),
        column(lambda found: float(found.documented)),
        column(lambda found: float(found.helper)),
        column(lambda found: float(found.kind == "type")),
        column(lambda found: float(found.kind == "law")),
        column(lambda found: float(found.base)),
        column(lambda found: len(last_part(found.name))),
        column(lambda found: found.name.count(".")),
        column(lambda found: math.log1p(found.length)),
        column(lambda found: found.module_best - found.code),
        column(lambda found: found.package_best - found.code),
        column(lambda found: modules[found.module]),
        column(parent_gap),
        column(children_gap),
        column(lambda found: math.log1p(len(found.children))),
        np.full(len(candidates), len(text.split()), dtype=np.float64),
        np.full(len(candidates), code.max(), dtype=np.float64),
    ]
    return np.column_stack(columns)
