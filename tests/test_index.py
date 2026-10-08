from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from jend.corpus import Entry
from jend.enrich import BATCH, MODEL, PROVIDER, Role, batches, request
from jend.index import Index, Quantized, Record, postings, tokens, write
from jend.loader import Definition
from jend.mirror import Package
from jend.parser import Kind


def test_tokens_split_identifiers_and_drop_stopwords() -> None:
    assert tokens("def HttpClient.get_body(url: String) for the URL") == [
        "def",
        "http",
        "client",
        "get",
        "body",
        "url",
        "string",
        "url",
    ]


def _record(row: int) -> Record:
    return Record(
        key=f"k{row}",
        name=f"f{row}",
        kind=Kind.DEF,
        signature=f"def f{row}() -> U32",
        doc=None,
        line=row + 1,
        path="a.bend",
        package_hash="0xa",
        package_name=None,
        package_version=None,
        package_rank=0,
        is_base=False,
        role=Role.API if row else None,
        summary=f"summary {row}" if row else None,
    )


def _index(directory: Path, texts: list[str]) -> Index:
    records = [_record(row) for row in range(len(texts))]
    vectors = np.eye(len(texts), 4, dtype=np.float32)
    write(directory, records, texts, vectors, vectors, "test")
    return Index(directory)


def test_bm25_ranks_the_rare_term_above_the_common_term(tmp_path: Path) -> None:
    index = _index(tmp_path / "index", ["parse json text", "parse toml text", "parse csv text"])

    scores = index.lexical("parse json")

    assert scores.argmax() == 0
    assert scores[1] == scores[2] > 0
    index.close()


def test_postings_skip_terms_that_a_text_does_not_contain() -> None:
    rows, _ = postings(["json", "toml json"])["toml"]

    assert rows.tolist() == [1]


def test_index_returns_records_in_the_order_of_the_rows(tmp_path: Path) -> None:
    index = _index(tmp_path / "index", ["a", "b", "c"])

    records = index.records(np.array([2, 0]))

    assert records == [_record(2), _record(0)]
    assert index.keys(np.array([1])) == ["k1"]
    assert list(index.scan()) == [_record(0), _record(1), _record(2)]
    index.close()


def test_quantized_scores_stay_close_to_the_exact_scores() -> None:
    generator = np.random.default_rng(0)
    vectors = generator.normal(size=(1000, 64)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    query = vectors[7]

    scores = Quantized.of(vectors).scores(query)

    assert np.abs(scores - vectors @ query).max() < 0.02
    assert scores.argmax() == 7


def _entry(package: str, path: str, signature: str) -> Entry:
    owner = Package(
        hash=package,
        name=None,
        version=None,
        description="",
        published=datetime(2026, 1, 1, tzinfo=UTC),
        hot=0.0,
        files=(),
    )
    definition = Definition(
        kind=Kind.DEF,
        name="f",
        key=f"{package}/{path}:f",
        signature=signature,
        doc="",
        line=1,
        first_line=0,
        last_line=0,
        refs=(),
    )
    return Entry(owner, 0, path, definition)


def test_batches_send_each_content_once_grouped_by_file() -> None:
    shared = _entry("0xa", "a.bend", "def f() -> U32")
    copy = _entry("0xb", "b.bend", "def f() -> U32")
    done = _entry("0xa", "a.bend", "def g() -> U32")
    many = [_entry("0xc", "c.bend", f"def h{n}() -> U32") for n in range(BATCH + 1)]

    work = batches([shared, copy, done, *many], {done.content_key})

    assert [[entry.id for entry in batch] for batch in work] == [
        [shared.id],
        [entry.id for entry in many[:BATCH]],
        [many[BATCH].id],
    ]


def test_request_uses_only_the_chosen_provider() -> None:
    body = request(MODEL, PROVIDER, [_entry("0xa", "a.bend", "def f() -> U32")])

    assert body["provider"] == {"order": ["open-inference/fp4"], "allow_fallbacks": False}
