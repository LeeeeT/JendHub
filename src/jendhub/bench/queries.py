import json
import re
from collections import Counter
from collections.abc import Container
from dataclasses import dataclass, field, replace

from jendhub.errors import JendHubError
from jendhub.json import Json

GRADES = (1, 2, 3)
JUDGED_GRADES = (0, 1, 2, 3)
BEST_GRADE = 3
ANSWER_FIELDS = {"id", "grade"}
JUDGMENT_FIELDS = {"id", "grade", "request"}
QUERY_FIELDS = {"query", "kind", "answers", "judgments"}
REQUEST = re.compile(r"[0-9a-f]{64}")

type Key = tuple[str, str]


class BenchError(JendHubError):
    pass


@dataclass(frozen=True)
class Answer:
    id: str
    grade: int


@dataclass(frozen=True)
class Judgment:
    id: str
    grade: int
    request: str


@dataclass(frozen=True)
class Query:
    text: str
    kind: str
    answers: list[Answer]
    judgments: list[Judgment] = field(default_factory=list[Judgment])


@dataclass(frozen=True)
class Assessment:
    text: str
    kind: str
    grades: dict[str, int]


def parse_answer(item: Json) -> Answer:
    match item:
        case {"id": str() as id_, "grade": int() as grade} if (
            grade in GRADES and item.keys() == ANSWER_FIELDS
        ):
            return Answer(id_, grade)
        case _:
            raise BenchError(f"invalid answer {item!r}: expected an id and a grade in {GRADES}")


def parse_judgment(item: Json) -> Judgment:
    match item:
        case {"id": str() as id_, "grade": int() as grade, "request": str() as request} if (
            grade in JUDGED_GRADES and REQUEST.fullmatch(request) and item.keys() == JUDGMENT_FIELDS
        ):
            return Judgment(id_, grade, request)
        case _:
            raise BenchError(
                f"invalid judgment {item!r}: expected an id, a grade in {JUDGED_GRADES}"
                " and the SHA-256 of the request in hexadecimal"
            )


def parse_query(line: str) -> Query:
    try:
        item: Json = json.loads(line)
    except json.JSONDecodeError as error:
        raise BenchError(f"invalid JSON: {error}") from error
    match item:
        case {
            "query": str() as query,
            "kind": str() as kind,
            "answers": list() as answers,
            "judgments": list() as judgments,
        } if query.strip() and kind.strip() and answers and item.keys() == QUERY_FIELDS:
            return Query(
                query,
                kind,
                [parse_answer(answer) for answer in answers],
                [parse_judgment(judgment) for judgment in judgments],
            )
        case _:
            raise BenchError("expected a non-empty query, kind and answers, and judgments")


def format_query(query: Query) -> str:
    item: Json = {
        "query": query.text,
        "kind": query.kind,
        "answers": [{"id": answer.id, "grade": answer.grade} for answer in query.answers],
        "judgments": [
            {"id": judgment.id, "grade": judgment.grade, "request": judgment.request}
            for judgment in query.judgments
        ],
    }
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def format_queries(queries: list[Query]) -> str:
    return "".join(format_query(query) + "\n" for query in queries)


def graded_ids(query: Query) -> list[str]:
    return [answer.id for answer in query.answers] + [j.id for j in query.judgments]


def query_problems(query: Query, ids: set[str]) -> list[str]:
    graded = graded_ids(query)
    problems = [f"unknown definition {id_!r}" for id_ in graded if id_ not in ids]
    duplicates = [id_ for id_, count in Counter(graded).items() if count > 1]
    problems += [f"two grades for {id_!r}" for id_ in duplicates]
    if all(answer.grade != BEST_GRADE for answer in query.answers):
        problems.append(f"no answer has grade {BEST_GRADE}")
    return problems


def check(text: str, ids: set[str]) -> tuple[list[Query], list[str]]:
    queries: list[Query] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            query = parse_query(line)
        except BenchError as error:
            problems.append(f"line {number}: {error}")
            continue
        if query.text in seen:
            problems.append(f"line {number}: duplicate query {query.text!r}")
        seen.add(query.text)
        problems += [f"line {number}: {problem}" for problem in query_problems(query, ids)]
        queries.append(query)
    return queries, problems


def queries_within(queries: list[Query], ids: set[str]) -> list[Query]:
    return [
        replace(query, judgments=[j for j in query.judgments if j.id in ids])
        for query in queries
        if all(answer.id in ids for answer in query.answers)
    ]


def with_judgments(query: Query, judgments: list[Judgment]) -> Query:
    found = {j.id: j for j in query.judgments} | {j.id: j for j in judgments}
    return replace(query, judgments=sorted(found.values(), key=lambda judgment: judgment.id))


def assessments(queries: list[Query], stale: Container[Key]) -> list[Assessment]:
    return [
        Assessment(
            query.text,
            query.kind,
            {j.id: j.grade for j in query.judgments if (query.text, j.id) not in stale}
            | {answer.id: answer.grade for answer in query.answers},
        )
        for query in queries
    ]


def summary(queries: list[Query]) -> str:
    kinds = Counter(query.kind for query in queries)
    grades = Counter(answer.grade for query in queries for answer in query.answers)
    lines = [f"queries: {len(queries)}"]
    lines += [f"  {kind}: {count}" for kind, count in kinds.most_common()]
    lines.append("answers by grade: " + ", ".join(f"{g}: {grades[g]}" for g in reversed(GRADES)))
    return "\n".join(lines)
