from datetime import UTC, datetime

import numpy as np

from jend.corpus import Entry
from jend.features import NAMES, Featurizer
from jend.index import Bm25, Document, Index
from jend.mirror import Package
from jend.signatures import Definition, Kind


def _document(name: str, path: str, signature: str, doc: str = "") -> Document:
    package = Package(
        hash="0x" + "b" * 32,
        name="bend-kit-json",
        version="1.0.0.0",
        description="JSON for Bend",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        hot=1.0,
        files=(),
    )
    entry = Entry(package, 1, path, Definition(Kind.DEF, name, signature, doc, 1))
    return Document(name, (entry,), None)


def _index(documents: list[Document]) -> Index:
    generator = np.random.default_rng(0)
    vectors = generator.normal(size=(len(documents), 8)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    texts = [document.text() for document in documents]
    return Index(tuple(documents), vectors, vectors.copy(), Bm25(texts), "test")


def test_candidates_have_one_column_for_each_feature_name() -> None:
    documents = [
        _document("Json.parse", "json.bend", "def Json.parse(s: String) -> Maybe<Json>"),
        _document("Json.parse.go", "json.bend", "def Json.parse.go(s: String) -> Json"),
        _document("parse_ok", "proofs/json.bend", "def parse_ok() -> Bool"),
    ]
    featurizer = Featurizer(_index(documents))

    candidates = featurizer.candidates("Json.parse", featurizer.index.text_vectors[0])

    assert candidates.features.shape == (3, len(NAMES))
    by_name = {
        documents[row].entry.definition.name: candidates.features[position]
        for position, row in enumerate(candidates.rows)
    }
    assert by_name["Json.parse"][NAMES.index("name_match")] == 1.0
    assert by_name["Json.parse.go"][NAMES.index("helper_name")] == 1.0
    assert by_name["Json.parse"][NAMES.index("helper_name")] == 0.0
    assert by_name["parse_ok"][NAMES.index("proof_file")] == 1.0
