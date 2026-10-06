from datetime import UTC, datetime

import numpy as np

from jend.corpus import Entry
from jend.enrich import Enrichment, Role
from jend.index import Bm25, Document, Index
from jend.mirror import Package
from jend.score import Scorer, is_helper, is_outside_api, names_match
from jend.signatures import Definition, Kind


def test_helper_names_follow_the_usual_conventions() -> None:
    assert is_helper("String.reverse.go")
    assert is_helper("parse_loop")
    assert is_helper("internal_sort_nat")
    assert is_helper("Map._balance")
    assert not is_helper("String.reverse")
    assert not is_helper("Map.ins_if")


def test_files_outside_the_api_exclude_shared_lemma_files() -> None:
    assert is_outside_api("spec/crypto/hmac.bend")
    assert is_outside_api("proofs/crypto/mac/hmac.bend")
    assert is_outside_api("packed_proof.bend")
    assert is_outside_api("tests/json.bend")
    assert not is_outside_api("proofs/lib/nat.bend")
    assert not is_outside_api("src/crypto/hash.bend")
    assert not is_outside_api("specification.bend")


def test_names_match_the_full_name_or_its_last_part() -> None:
    assert names_match("Map.get", "map.get")
    assert names_match("hash", "hash")
    assert names_match("List.sort", "sort")
    assert names_match("get", "map.get")
    assert not names_match("hash_acc", "hash")


def _document(name: str, path: str, kind: Kind = Kind.DEF) -> Document:
    package = Package(
        hash="0x" + "b" * 32,
        name="bend-kit-hash",
        version="1.0.0.0",
        description="Hash functions",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        hot=1.0,
        files=(),
    )
    signature = f"def {name}(b: Bytes) -> U64"
    return Document(
        name, (Entry(package, 1, path, Definition(kind, name, signature, "", 1)),), None
    )


def test_rank_prefers_the_api_over_helpers_and_spec_copies_with_the_same_vector() -> None:
    documents = [
        _document("hash.go", "hash.bend"),
        _document("hash", "spec/hash.bend"),
        _document("hash", "hash.bend"),
        _document("unrelated", "other.bend"),
    ]
    vectors = np.zeros((4, 4), dtype=np.float32)
    vectors[:3, 0] = 1.0
    vectors[3, 1] = 1.0
    texts = [document.text() for document in documents]
    scorer = Scorer(Index(tuple(documents), vectors, vectors.copy(), Bm25(texts), "test"))

    ranking = scorer.rank("hash", np.array([1, 0, 0, 0], dtype=np.float32))

    assert [int(row) for row in ranking.rows] == [2, 1, 0, 3]
    assert np.all(np.diff(ranking.scores) <= 0)


def test_rank_puts_a_definition_with_the_api_role_before_a_helper_with_the_same_signals() -> None:
    def enriched(name: str, role: Role) -> Document:
        document = _document(name, "crc.bend")
        enrichment = Enrichment(
            key=name, role=role, summary="", queries=(), keywords=(), model="test"
        )
        return Document(document.key, document.entries, enrichment)

    documents = [enriched("crc", Role.HELPER), enriched("crc32", Role.API)]
    vectors = np.zeros((2, 4), dtype=np.float32)
    vectors[:, 0] = 1.0
    texts = [document.text() for document in documents]
    scorer = Scorer(Index(tuple(documents), vectors, vectors.copy(), Bm25(texts), "test"))

    ranking = scorer.rank("checksum", np.array([1, 0, 0, 0], dtype=np.float32))

    assert [int(row) for row in ranking.rows] == [1, 0]
