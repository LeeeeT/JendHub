from pathlib import Path

import numpy as np

from jend.enrich import Role
from jend.index import Index, Record, write
from jend.score import Scorer, is_helper, is_outside_api, names_match
from jend.signatures import Kind


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


def _record(name: str, path: str, role: Role | None = None) -> Record:
    return Record(
        key=f"{path}:{name}",
        name=name,
        kind=Kind.DEF,
        signature=f"def {name}(b: Bytes) -> U64",
        line=1,
        path=path,
        package_hash="0x" + "b" * 32,
        package_name="bend-kit-hash",
        package_version="1.0.0.0",
        package_rank=1,
        is_base=False,
        role=role,
        summary=None,
    )


def _scorer(directory: Path, records: list[Record], vectors: np.ndarray) -> Scorer:
    texts = [f"{record.name}\n{record.signature}" for record in records]
    write(directory, records, texts, vectors, vectors.copy(), "test")
    return Scorer(Index(directory))


def test_rank_prefers_the_api_over_helpers_and_spec_copies_with_the_same_vector(
    tmp_path: Path,
) -> None:
    records = [
        _record("hash.go", "hash.bend"),
        _record("hash", "spec/hash.bend"),
        _record("hash", "hash.bend"),
        _record("unrelated", "other.bend"),
    ]
    vectors = np.zeros((4, 4), dtype=np.float32)
    vectors[:3, 0] = 1.0
    vectors[3, 1] = 1.0
    scorer = _scorer(tmp_path / "index", records, vectors)

    ranking = scorer.rank("hash", np.array([1, 0, 0, 0], dtype=np.float32))

    assert [int(row) for row in ranking.rows] == [2, 1, 0, 3]
    assert np.all(np.diff(ranking.scores) <= 0)
    scorer.index.close()


def test_rank_puts_a_definition_with_the_api_role_before_a_helper_with_the_same_signals(
    tmp_path: Path,
) -> None:
    records = [_record("crc", "crc.bend", Role.HELPER), _record("crc32", "crc.bend", Role.API)]
    vectors = np.zeros((2, 4), dtype=np.float32)
    vectors[:, 0] = 1.0
    scorer = _scorer(tmp_path / "index", records, vectors)

    ranking = scorer.rank("checksum", np.array([1, 0, 0, 0], dtype=np.float32))

    assert [int(row) for row in ranking.rows] == [1, 0]
    scorer.index.close()
