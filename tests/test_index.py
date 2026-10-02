from datetime import UTC, datetime

from jend.corpus import Entry
from jend.enrich import BATCH, batches
from jend.index import Bm25, tokens
from jend.mirror import Package
from jend.signatures import Definition, Kind


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


def test_bm25_ranks_the_rare_term_above_the_common_term() -> None:
    index = Bm25(["parse json text", "parse toml text", "parse csv text"])

    scores = index.scores("parse json")

    assert scores.argmax() == 0
    assert scores[1] == scores[2] > 0


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
    return Entry(owner, 0, path, Definition(Kind.DEF, "f", signature, "", 1))


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
