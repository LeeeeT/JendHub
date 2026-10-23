import json

from jendhub.bench.queries import (
    Answer,
    Assessment,
    Judgment,
    Query,
    assessments,
    check,
    format_queries,
    queries_within,
    summary,
    with_judgments,
)

KIT = "0x" + "a" * 32
EXTRA = "0x" + "b" * 32
IDS = {"List.map", "List.fold", "kit@0.1.0.0/json:parse"}
REQUEST = "0" * 64
OTHER_REQUEST = "f" * 64


def line(
    query: str,
    kind: str,
    answers: list[tuple[str, int]],
    judgments: list[tuple[str, int, str]] | None = None,
) -> str:
    items = [{"id": id_, "grade": grade} for id_, grade in answers]
    judged = [{"id": id_, "grade": grade, "request": r} for id_, grade, r in judgments or []]
    return json.dumps({"query": query, "kind": kind, "answers": items, "judgments": judged})


def test_check_reads_valid_queries() -> None:
    answers = [("List.map", 3), ("kit@0.1.0.0/json:parse", 1)]
    text = line("map a list", "description", answers, [("List.fold", 0, REQUEST)])
    queries, problems = check(text + "\n\n", IDS)
    assert problems == []
    assert queries == [
        Query(
            "map a list",
            "description",
            [Answer("List.map", 3), Answer("kit@0.1.0.0/json:parse", 1)],
            [Judgment("List.fold", 0, REQUEST)],
        )
    ]


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
            json.dumps({"query": "x", "kind": "name", "answers": [{"id": "List.map", "grade": 3}]}),
            line("judged", "name", [("List.map", 3)], [("List.map", 1, REQUEST)]),
            line("unknown", "name", [("List.map", 3)], [("List.sort", 0, REQUEST)]),
            line("short", "name", [("List.map", 3)], [("List.fold", 0, "abc")]),
        ]
    )
    _, problems = check(text, IDS)
    expected = [
        "line 2: duplicate query 'map'",
        "line 3: unknown definition 'Json.parse'",
        "line 3: two grades for 'List.map'",
        "line 4: no answer has grade 3",
        "line 5: invalid answer {'id': 'List.map', 'grade': 4}",
        "line 6: invalid JSON",
        "line 7: expected a non-empty query, kind and answers, and judgments",
        "line 8: expected a non-empty query, kind and answers, and judgments",
        "line 9: two grades for 'List.map'",
        "line 10: unknown definition 'List.sort'",
        "line 11: invalid judgment",
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


def test_queries_within_drop_the_judgments_outside_the_ids() -> None:
    judged = Query(
        "map a list",
        "phrase",
        [Answer("List.map", 3)],
        [Judgment("List.fold", 1, REQUEST), Judgment("x@0.1.0.0/x:y", 0, REQUEST)],
    )
    assert queries_within([judged], IDS) == [
        Query("map a list", "phrase", [Answer("List.map", 3)], [Judgment("List.fold", 1, REQUEST)])
    ]


def test_format_queries_writes_what_check_reads() -> None:
    queries = [
        Query("map a list", "phrase", [Answer("List.map", 3)], [Judgment("List.fold", 0, REQUEST)]),
        Query("parse", "name", [Answer("kit@0.1.0.0/json:parse", 3)]),
    ]
    assert check(format_queries(queries), IDS) == (queries, [])


def test_with_judgments_replaces_the_judgment_of_the_same_definition() -> None:
    query = Query(
        "map a list",
        "phrase",
        [Answer("List.map", 3)],
        [Judgment("List.fold", 0, REQUEST), Judgment("Nat.add", 0, REQUEST)],
    )
    judged = with_judgments(
        query, [Judgment("List.fold", 2, OTHER_REQUEST), Judgment("List.all", 1, REQUEST)]
    )
    assert judged.judgments == [
        Judgment("List.all", 1, REQUEST),
        Judgment("List.fold", 2, OTHER_REQUEST),
        Judgment("Nat.add", 0, REQUEST),
    ]


def test_assessments_join_the_answers_and_the_current_judgments() -> None:
    query = Query(
        "map a list",
        "phrase",
        [Answer("List.map", 3)],
        [Judgment("List.fold", 0, REQUEST), Judgment("Nat.add", 2, REQUEST)],
    )
    assert assessments([query], {("map a list", "Nat.add")}) == [
        Assessment("map a list", "phrase", {"List.fold": 0, "List.map": 3})
    ]
