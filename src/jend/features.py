import math
import re
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jend.index import Bm25, Document, Index, Scores, tokens

POOL_DEPTH = 200
STAGE_DEPTH = 100

Matrix = npt.NDArray[np.float32]
Rows = npt.NDArray[np.intp]
Vector = npt.NDArray[np.float32]

HELPER_NAME = re.compile(
    r"\.(go|loop|step|if|aux|acc|rec|helper|from|walk|impl|inner|run|cont|k|lp|body|case|match"
    r"|fold|worker|tail)\d*$"
    r"|_(go|loop|step|aux|acc|rec|helper|walk|impl|inner|worker)\d*$"
    r"|^internal_|^_|\.internal_|'$"
)
STATEMENT_QUERY = re.compile(
    r"==|\bproof\b|\bfor all\b|\bforall\b|\bimplies\b|\blemma\b|\btheorem\b"
    r"|produces a sorted|\bis commutative\b|\bdivides\b"
)
TYPE_TOKEN = re.compile(r"[A-Z][A-Za-z0-9]*|->|<|>")
PROOF_FILE = re.compile(r"(^|/)proofs?(/|\.bend$)|proof\.bend$")
PROOF_LIBRARY = re.compile(r"(^|/)proofs?/lib/")
SPEC_FILE = re.compile(r"(^|/)specs?(/|\.bend$)|_spec\.bend$")
TEST_FILE = re.compile(r"test|usage|example|bench|conformance|demo")

FIELDS = ("name", "doc", "enrichment", "signature", "location")
QUERY_FEATURES = (
    "vector",
    "vector_gap",
    "vector_rank",
    "bm25",
    "bm25_gap",
    "bm25_rank",
    *(f"bm25_{field}" for field in FIELDS),
    "name_coverage",
    "name_match",
    "type_overlap",
    "law_for_statement",
    "statement_query",
    "signature_query",
    "signature_vector",
    "signature_vector_gap",
)
DOCUMENT_FEATURES = (
    "proof_file",
    "proof_library",
    "spec_file",
    "test_file",
    "helper_name",
    "name_depth",
    "law",
    "type",
    "documented",
    "doc_length",
    "base",
    "package_rank",
    "copies",
    "signature_length",
    "unenriched",
)
NAMES = QUERY_FEATURES + DOCUMENT_FEATURES


def normalize(query: str) -> str:
    return " ".join(query.lower().split())


def _field(document: Document, field: str) -> str:
    entry = document.entry
    definition = entry.definition
    enrichment = document.enrichment
    match field:
        case "name":
            return definition.name.replace(".", " ").replace("_", " ")
        case "doc":
            return definition.doc
        case "enrichment":
            return "" if enrichment is None else " ".join([enrichment.summary, *enrichment.queries])
        case "signature":
            return definition.signature[:800]
        case _:
            return f"{entry.package_label} {entry.path} {entry.package.description[:200]}"


def _document_features(document: Document) -> list[float]:
    entry = document.entry
    definition = entry.definition
    path = entry.path.lower()
    library = bool(PROOF_LIBRARY.search(path))
    return [
        float(bool(PROOF_FILE.search(path)) and not library),
        float(library),
        float(bool(SPEC_FILE.search(path))),
        float(bool(TEST_FILE.search(path))),
        float(bool(HELPER_NAME.search(definition.name))),
        float(definition.name.count(".")),
        float(definition.kind.value == "law"),
        float(definition.kind.value == "type"),
        float(bool(definition.doc)),
        math.log1p(len(definition.doc)),
        float(entry.package.is_base),
        math.log1p(entry.rank),
        math.log(len(document.entries)),
        math.log1p(len(definition.signature)),
        float(document.enrichment is None),
    ]


def positions(rows: Rows) -> list[int]:
    values: list[int] = rows.tolist()
    return values


def _top(scores: Scores, depth: int) -> Rows:
    depth = min(depth, len(scores))
    rows = np.argpartition(-scores, depth - 1)[:depth]
    return rows[np.argsort(-scores[rows], kind="stable")]


def _matches(scores: Scores, depth: int) -> Rows:
    rows = _top(scores, depth)
    return rows[scores[rows] > 0]


def _ranks(scores: Scores, rows: Rows) -> Matrix:
    ranks = np.empty(len(scores), dtype=np.float32)
    ranks[np.argsort(-scores, kind="stable")] = np.arange(len(scores), dtype=np.float32)
    return np.log1p(ranks[rows])


@dataclass(frozen=True)
class Candidates:
    rows: Rows
    features: Matrix
    lexical: Rows
    semantic: Rows


class Featurizer:
    def __init__(self, index: Index) -> None:
        self.index = index
        documents = index.documents
        self.fields = {
            field: Bm25([_field(document, field) for document in documents]) for field in FIELDS
        }
        self.document_features = np.array(
            [_document_features(document) for document in documents], dtype=np.float32
        )
        self.name_tokens = [frozenset(tokens(d.entry.definition.name)) for d in documents]
        self.names = [document.entry.definition.name.lower() for document in documents]
        self.type_tokens = [
            frozenset(TYPE_TOKEN.findall(d.entry.definition.signature)) for d in documents
        ]

    def candidates(self, query: str, vector: Vector) -> Candidates:
        text = normalize(query)
        semantic = self.index.text_vectors @ vector
        lexical = self.index.bm25.scores(text)
        rows = np.union1d(_top(semantic, POOL_DEPTH), _matches(lexical, POOL_DEPTH))
        features = np.hstack(
            [
                self._query_features(query, text, vector, semantic, lexical, rows),
                self.document_features[rows],
            ]
        )
        return Candidates(
            rows, features, _matches(lexical, STAGE_DEPTH), _top(semantic, STAGE_DEPTH)
        )

    def _query_features(
        self, query: str, text: str, vector: Vector, semantic: Scores, lexical: Scores, rows: Rows
    ) -> Matrix:
        lexical_top = max(float(lexical.max()), 1e-6)
        fields = [self.fields[field].scores(text)[rows] for field in FIELDS]
        signature = self.index.signature_vectors[rows] @ vector
        words = frozenset(tokens(text))
        identifier = query.strip().lower()
        statement = bool(STATEMENT_QUERY.search(text))
        typed = "->" in query
        query_types = frozenset(TYPE_TOKEN.findall(query))
        rows_list = positions(rows)
        name_coverage = [
            len(words & self.name_tokens[row]) / len(words) if words else 0.0 for row in rows_list
        ]
        name_match = [
            float(
                self.names[row] == identifier
                or self.names[row].endswith("." + identifier)
                or identifier.endswith("." + self.names[row])
            )
            for row in rows_list
        ]
        type_overlap = [
            len(query_types & self.type_tokens[row]) / len(query_types | self.type_tokens[row])
            if typed and query_types
            else 0.0
            for row in rows_list
        ]
        law = self.document_features[rows, DOCUMENT_FEATURES.index("law")]
        columns = [
            semantic[rows],
            semantic[rows] - semantic.max(),
            _ranks(semantic, rows),
            lexical[rows] / lexical_top,
            lexical[rows] - lexical_top,
            _ranks(lexical, rows),
            *(scores / max(float(scores.max()), 1e-6) for scores in fields),
            np.array(name_coverage),
            np.array(name_match),
            np.array(type_overlap),
            law * statement,
            np.full(len(rows), float(statement)),
            np.full(len(rows), float(typed)),
            signature,
            signature - signature.max(),
        ]
        return np.stack(columns, axis=1).astype(np.float32)
