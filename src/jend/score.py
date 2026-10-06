import re
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jend.index import Document, Index, Scores, tokens

POOL_DEPTH = 200
STAGE_DEPTH = 100

TEXT_WEIGHT = 1.0
SIGNATURE_WEIGHT = 0.5
KEYWORD_WEIGHT = 0.5
NAME_WEIGHT = 1.0
EXACT_NAME_BONUS = 1.0
BASE_BONUS = 0.25
HELPER_PENALTY = 1.0
NON_API_FILE_PENALTY = 1.0
LAW_PENALTY = 0.5

Rows = npt.NDArray[np.intp]
Vector = npt.NDArray[np.float32]
Values = npt.NDArray[np.float64]

HELPER_NAME = re.compile(
    r"\.(go|loop|step|if|aux|acc|rec|helper|impl|inner|worker)\d*$"
    r"|_(go|loop|step|aux|acc|rec|helper|impl|inner|worker)\d*$"
    r"|(^|\.)_|(^|\.)internal_"
)
NON_API_FILE = re.compile(
    r"(^|/|_)(proofs?|specs?|tests?|examples?|usage|bench(marks?)?|conformance|demos?)"
    r"(/|_|\.bend$)"
)
SHARED_LEMMA_FILE = re.compile(r"(^|/)proofs?/lib/")
STATEMENT_QUERY = re.compile(
    r"==|!=|\bfor all\b|\bforall\b|\bproof\b|\blemma\b|\btheorem\b|\blaw\b"
)


def normalize(query: str) -> str:
    return " ".join(query.lower().split())


def positions(rows: Rows) -> list[int]:
    values: list[int] = rows.tolist()
    return values


def is_helper(name: str) -> bool:
    return bool(HELPER_NAME.search(name))


def is_outside_api(path: str) -> bool:
    lowered = path.lower()
    return bool(NON_API_FILE.search(lowered)) and not SHARED_LEMMA_FILE.search(lowered)


def names_match(name: str, query: str) -> bool:
    name = name.lower()
    return (
        name == query
        or name.rsplit(".", 1)[-1] == query
        or name.endswith("." + query)
        or query.endswith("." + name)
    )


def _top(scores: Scores, depth: int) -> Rows:
    depth = min(depth, len(scores))
    rows = np.argpartition(-scores, depth - 1)[:depth]
    return rows[np.argsort(-scores[rows], kind="stable")]


def _matches(scores: Scores, depth: int) -> Rows:
    rows = _top(scores, depth)
    return rows[scores[rows] > 0]


def _standard(values: Values) -> Values:
    spread = float(values.std())
    if spread < 1e-9:
        return np.zeros_like(values)
    return (values - values.mean()) / spread


@dataclass(frozen=True)
class Ranking:
    rows: Rows
    scores: Values
    lexical: Rows
    semantic: Rows


class Scorer:
    def __init__(self, index: Index) -> None:
        self.index = index
        documents = index.documents
        self.name_tokens = [frozenset(tokens(d.entry.definition.name)) for d in documents]
        self.prior = np.array([_prior(document) for document in documents], dtype=np.float64)
        self.law = np.array([d.entry.definition.kind.value == "law" for d in documents])
        self.package_rank = np.array([d.entry.rank for d in documents])

    def rank(self, query: str, vector: Vector) -> Ranking:
        text = normalize(query)
        semantic = self.index.text_vectors @ vector
        lexical = self.index.bm25.scores(text)
        rows = np.union1d(_top(semantic, POOL_DEPTH), _matches(lexical, POOL_DEPTH))
        scores = self._scores(text, vector, semantic, lexical, rows)
        best = np.lexsort((self.package_rank[rows], -scores))
        return Ranking(
            rows[best], scores[best], _matches(lexical, STAGE_DEPTH), _top(semantic, STAGE_DEPTH)
        )

    def _scores(
        self, text: str, vector: Vector, semantic: Scores, lexical: Scores, rows: Rows
    ) -> Values:
        words = frozenset(tokens(text))
        documents = self.index.documents
        names = [
            len(words & self.name_tokens[row]) / len(self.name_tokens[row])
            if self.name_tokens[row]
            else 0.0
            for row in positions(rows)
        ]
        exact = [names_match(documents[row].entry.definition.name, text) for row in positions(rows)]
        statement = bool(STATEMENT_QUERY.search(text))
        signature = self.index.signature_vectors[rows] @ vector
        return (
            TEXT_WEIGHT * _standard(semantic[rows].astype(np.float64))
            + SIGNATURE_WEIGHT * _standard(signature.astype(np.float64))
            + KEYWORD_WEIGHT * _standard(lexical[rows].astype(np.float64))
            + NAME_WEIGHT * np.array(names)
            + EXACT_NAME_BONUS * np.array(exact, dtype=np.float64)
            + self.prior[rows]
            - LAW_PENALTY * (self.law[rows] & (not statement))
        )


def _prior(document: Document) -> float:
    entry = document.entry
    return (
        BASE_BONUS * entry.package.is_base
        - HELPER_PENALTY * is_helper(entry.definition.name)
        - NON_API_FILE_PENALTY * is_outside_api(entry.path)
    )
