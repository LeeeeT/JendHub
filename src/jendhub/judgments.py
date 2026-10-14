import json
from dataclasses import dataclass

from jendhub.bench import BenchError, Query
from jendhub.hub import Json

JUDGED_GRADES = (0, 1, 2, 3)
JUDGMENT_FIELDS = {"query", "id", "grade", "judge"}


@dataclass(frozen=True)
class Judgment:
    query: str
    id: str
    grade: int
    judge: str


@dataclass(frozen=True)
class Assessment:
    query: str
    kind: str
    grades: dict[str, int]


def parse_judgment(line: str) -> Judgment:
    try:
        item: Json = json.loads(line)
    except json.JSONDecodeError as error:
        raise BenchError(f"invalid JSON: {error}") from error
    match item:
        case {
            "query": str() as query,
            "id": str() as id_,
            "grade": int() as grade,
            "judge": str() as judge,
        } if grade in JUDGED_GRADES and item.keys() == JUDGMENT_FIELDS:
            return Judgment(query, id_, grade, judge)
        case _:
            raise BenchError(f"expected a query, an id, a grade in {JUDGED_GRADES} and a judge")


def judgment_problems(
    judgment: Judgment, queries: set[str], ids: set[str], judge: str
) -> list[str]:
    problems: list[str] = []
    if judgment.query not in queries:
        problems.append(f"unknown query {judgment.query!r}")
    if judgment.id not in ids:
        problems.append(f"unknown definition {judgment.id!r}")
    if judgment.judge != judge:
        problems.append(f"another judge {judgment.judge!r}: delete the judgments of old judges")
    return problems


def judgments_within(
    judgments: list[Judgment], queries: list[Query], ids: set[str]
) -> list[Judgment]:
    kept = {query.query for query in queries}
    return [j for j in judgments if j.query in kept and j.id in ids]


def judgment_line(judgment: Judgment) -> str:
    item = {
        "query": judgment.query,
        "id": judgment.id,
        "grade": judgment.grade,
        "judge": judgment.judge,
    }
    return json.dumps(item, ensure_ascii=False) + "\n"


def check_judgments(
    text: str, queries: list[Query], ids: set[str], judge: str
) -> tuple[list[Judgment], list[str]]:
    known = {query.query for query in queries}
    judgments: list[Judgment] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            judgment = parse_judgment(line)
        except BenchError as error:
            problems.append(f"line {number}: {error}")
            continue
        found = judgment_problems(judgment, known, ids, judge)
        if (judgment.query, judgment.id) in seen:
            found.append(f"duplicate judgment of {judgment.id!r}")
        problems += [f"line {number}: {problem}" for problem in found]
        seen.add((judgment.query, judgment.id))
        judgments.append(judgment)
    return judgments, problems


def assessments(queries: list[Query], judgments: list[Judgment]) -> list[Assessment]:
    judged: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        judged.setdefault(judgment.query, {})[judgment.id] = judgment.grade
    return [
        Assessment(
            query.query,
            query.kind,
            judged.get(query.query, {}) | {answer.id: answer.grade for answer in query.answers},
        )
        for query in queries
    ]
