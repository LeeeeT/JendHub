import re
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jend.enrich import Role
from jend.index import Index, Record, Rows, Scores, Vector, tokens
from jend.parser import Tag

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
ROLE_PENALTY = 1.0

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
        prior: list[float] = []
        law: list[bool] = []
        package_rank: list[int] = []
        for record in index.scan():
            prior.append(_prior(record))
            law.append(record.kind is Tag.LAW)
            package_rank.append(record.package_rank)
        self.prior = np.array(prior, dtype=np.float64)
        self.law = np.array(law, dtype=np.bool_)
        self.package_rank = np.array(package_rank, dtype=np.int32)

    def rank(self, query: str, vector: Vector) -> Ranking:
        text = normalize(query)
        semantic = self.index.semantic(vector)
        lexical = self.index.lexical(text)
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
        names = self.index.names(rows)
        coverage = [_coverage(words, frozenset(tokens(name))) for name in names]
        exact = [names_match(name, text) for name in names]
        statement = bool(STATEMENT_QUERY.search(text))
        signature = self.index.signature_scores(rows, vector)
        return (
            TEXT_WEIGHT * _standard(semantic[rows].astype(np.float64))
            + SIGNATURE_WEIGHT * _standard(signature.astype(np.float64))
            + KEYWORD_WEIGHT * _standard(lexical[rows].astype(np.float64))
            + NAME_WEIGHT * np.array(coverage, dtype=np.float64)
            + EXACT_NAME_BONUS * np.array(exact, dtype=np.float64)
            + self.prior[rows]
            - LAW_PENALTY * (self.law[rows] & (not statement))
        )


def _coverage(words: frozenset[str], name: frozenset[str]) -> float:
    return len(words & name) / len(name) if name else 0.0


def _prior(record: Record) -> float:
    return (
        BASE_BONUS * record.is_base
        - HELPER_PENALTY * is_helper(record.name)
        - NON_API_FILE_PENALTY * is_outside_api(record.path)
        - ROLE_PENALTY * outside_api_role(record)
    )


def outside_api_role(record: Record) -> bool:
    return record.role is not None and record.role is not Role.API
