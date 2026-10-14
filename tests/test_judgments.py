import pytest

from jendhub.bench import Answer, BenchError, Query
from jendhub.judgments import (
    Assessment,
    Judgment,
    assessments,
    check_judgments,
    judgment_line,
    judgments_within,
    parse_judgment,
)

JUDGE = "judge-a"
MAP = Query("map a list", "phrase", [Answer("List.map", 3)])
IDS = {"List.map", "List.fold", "Nat.add"}


def test_judgment_line_round_trips() -> None:
    judgment = Judgment("map a list", "List.fold", 0, JUDGE)
    assert parse_judgment(judgment_line(judgment)) == judgment


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"query": "q", "id": "a", "grade": 4, "judge": "j"}',
        '{"query": "q", "id": "a", "grade": 1}',
        '{"query": "q", "id": "a", "grade": 1, "judge": "j", "note": ""}',
    ],
)
def test_parse_judgment_refuses_an_invalid_line(line: str) -> None:
    with pytest.raises(BenchError):
        parse_judgment(line)


def test_check_judgments_reads_valid_lines() -> None:
    text = judgment_line(Judgment("map a list", "List.fold", 1, JUDGE))
    judgments, problems = check_judgments(text, [MAP], IDS, JUDGE)
    assert judgments == [Judgment("map a list", "List.fold", 1, JUDGE)]
    assert problems == []


def test_check_judgments_reports_each_problem() -> None:
    lines = [
        Judgment("sort a list", "List.fold", 1, JUDGE),
        Judgment("map a list", "List.sort", 1, JUDGE),
        Judgment("map a list", "Nat.add", 0, JUDGE),
        Judgment("map a list", "Nat.add", 0, JUDGE),
        Judgment("map a list", "List.fold", 2, "judge-b"),
    ]
    _, problems = check_judgments("".join(map(judgment_line, lines)), [MAP], IDS, JUDGE)
    assert problems == [
        "line 1: unknown query 'sort a list'",
        "line 2: unknown definition 'List.sort'",
        "line 4: duplicate judgment of 'Nat.add'",
        "line 5: another judge 'judge-b': delete the judgments of old judges",
    ]


def test_assessments_keep_the_original_grade_over_a_judgment() -> None:
    judgments = [
        Judgment("map a list", "List.map", 1, JUDGE),
        Judgment("map a list", "List.fold", 0, JUDGE),
    ]
    assert assessments([MAP], judgments) == [
        Assessment("map a list", "phrase", {"List.fold": 0, "List.map": 3})
    ]


def test_judgments_within_keep_the_judgments_of_kept_queries_and_ids() -> None:
    kept = Judgment("map a list", "List.fold", 1, JUDGE)
    other_id = Judgment("map a list", "Nat.sub", 0, JUDGE)
    other_query = Judgment("sort a list", "List.fold", 2, JUDGE)
    assert judgments_within([kept, other_id, other_query], [MAP], IDS) == [kept]
