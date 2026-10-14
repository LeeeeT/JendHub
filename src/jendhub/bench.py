import json
from collections import Counter
from dataclasses import dataclass

from jendhub.definitions import Corpus, source_files
from jendhub.errors import JendHubError
from jendhub.hub import Json

GRADES = (1, 2, 3)
BEST_GRADE = 3
ANSWER_FIELDS = {"id", "grade"}
QUERY_FIELDS = {"query", "kind", "answers"}


class BenchError(JendHubError):
    pass


@dataclass(frozen=True)
class Answer:
    id: str
    grade: int


@dataclass(frozen=True)
class Query:
    query: str
    kind: str
    answers: list[Answer]


def corpus_text(corpus: Corpus) -> str:
    sections = [
        f"===== {file.origin.label} =====\n{file.source.rstrip()}\n"
        for file in source_files(corpus)
    ]
    return "\n".join(sections)


def parse_answer(item: Json) -> Answer:
    match item:
        case {"id": str() as id_, "grade": int() as grade} if (
            grade in GRADES and item.keys() == ANSWER_FIELDS
        ):
            return Answer(id_, grade)
        case _:
            raise BenchError(f"invalid answer {item!r}: expected an id and a grade in {GRADES}")


def parse_query(line: str) -> Query:
    try:
        item: Json = json.loads(line)
    except json.JSONDecodeError as error:
        raise BenchError(f"invalid JSON: {error}") from error
    match item:
        case {"query": str() as query, "kind": str() as kind, "answers": list() as answers} if (
            query.strip() and kind.strip() and answers and item.keys() == QUERY_FIELDS
        ):
            return Query(query, kind, [parse_answer(answer) for answer in answers])
        case _:
            raise BenchError("expected a non-empty query, kind and answers")


def query_problems(query: Query, ids: set[str]) -> list[str]:
    problems = [
        f"unknown definition {answer.id!r}" for answer in query.answers if answer.id not in ids
    ]
    duplicates = [id_ for id_, count in Counter(a.id for a in query.answers).items() if count > 1]
    problems += [f"duplicate answer {id_!r}" for id_ in duplicates]
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
        if query.query in seen:
            problems.append(f"line {number}: duplicate query {query.query!r}")
        seen.add(query.query)
        problems += [f"line {number}: {problem}" for problem in query_problems(query, ids)]
        queries.append(query)
    return queries, problems


def queries_within(queries: list[Query], ids: set[str]) -> list[Query]:
    return [query for query in queries if all(answer.id in ids for answer in query.answers)]


def summary(queries: list[Query]) -> str:
    kinds = Counter(query.kind for query in queries)
    grades = Counter(answer.grade for query in queries for answer in query.answers)
    lines = [f"queries: {len(queries)}"]
    lines += [f"  {kind}: {count}" for kind, count in kinds.most_common()]
    lines.append("answers by grade: " + ", ".join(f"{g}: {grades[g]}" for g in reversed(GRADES)))
    return "\n".join(lines)
