import json

from jendhub.bench.queries import Answer, Query, check, queries_within, summary

KIT = "0x" + "a" * 32
EXTRA = "0x" + "b" * 32
IDS = {"List.map", "kit@0.1.0.0/json:parse"}


def line(query: str, kind: str, answers: list[tuple[str, int]]) -> str:
    items = [{"id": id_, "grade": grade} for id_, grade in answers]
    return json.dumps({"query": query, "kind": kind, "answers": items})


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


def test_queries_within_keep_the_queries_with_each_answer_in_the_ids() -> None:
    inside = Query("map a list", "phrase", [Answer("List.map", 3)])
    outside = Query("parse json", "keywords", [Answer("List.map", 1), Answer("x@0.1.0.0/x:y", 3)])
    assert queries_within([inside, outside], IDS) == [inside]
