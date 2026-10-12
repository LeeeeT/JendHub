import json
from pathlib import Path

from jendhub.base import Base
from jendhub.bench import Answer, Query, check, corpus_text, summary
from jendhub.hub import Listing, Package, Release
from jendhub.mirror import Mirror

KIT = "0x" + "a" * 32
IDS = {"List.map", "kit@0.1.0.0/json:parse"}


def line(query: str, kind: str, answers: list[tuple[str, int]]) -> str:
    items = [{"id": id_, "grade": grade} for id_, grade in answers]
    return json.dumps({"query": query, "kind": kind, "answers": items})


def corpus(root: Path) -> Mirror:
    mirror = Mirror(root)
    mirror.store_base(Base("v2.0.36", b"def List.map(xs: List) -> List:\n  xs\n"))
    files = {"json.bend": b"def parse(s: String) -> Json:\n  s\n"}
    mirror.store_package(Package(Listing(KIT, Release("kit", "0.1.0.0")), files))
    return mirror


def test_corpus_text_heads_each_file_with_its_id_prefix(tmp_path: Path) -> None:
    text = corpus_text(corpus(tmp_path))
    assert text.startswith("===== Base =====\ndef List.map")
    assert "===== kit@0.1.0.0/json =====\ndef parse" in text


def test_check_reads_valid_queries() -> None:
    text = line("map a list", "description", [("List.map", 3), ("kit@0.1.0.0/json:parse", 1)])
    queries, problems = check(text + "\n\n", IDS)
    assert problems == []
    answers = [Answer("List.map", 3), Answer("kit@0.1.0.0/json:parse", 1)]
    assert queries == [Query("map a list", "description", answers)]


def test_check_reports_each_problem_with_its_line() -> None:
    text = "\n".join(
        [
            line("map", "name", [("List.map", 3)]),
            line("map", "name", [("List.map", 3)]),
            line("parse", "name", [("Json.parse", 3), ("List.map", 3), ("List.map", 2)]),
            line("weak", "keywords", [("List.map", 2)]),
            line("graded", "name", [("List.map", 4)]),
            "{not json",
            json.dumps({"query": "x", "kind": "name", "answers": [], "extra": 1}),
        ]
    )
    _, problems = check(text, IDS)
    expected = [
        "line 2: duplicate query 'map'",
        "line 3: unknown definition 'Json.parse'",
        "line 3: duplicate answer 'List.map'",
        "line 4: no answer has grade 3",
        "line 5: invalid answer {'id': 'List.map', 'grade': 4}",
        "line 6: invalid JSON",
        "line 7: expected a non-empty query, kind and answers",
    ]
    assert all(map(str.startswith, problems, expected, strict=True))


def test_summary_counts_kinds_and_grades() -> None:
    queries = [
        Query("a", "name", [Answer("x", 3)]),
        Query("b", "name", [Answer("x", 3), Answer("y", 1)]),
        Query("c", "description", [Answer("x", 2), Answer("y", 3)]),
    ]
    assert summary(queries) == (
        "queries: 3\n  name: 2\n  description: 1\nanswers by grade: 3: 3, 2: 1, 1: 1"
    )
