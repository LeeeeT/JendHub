import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from jendhub.bench import Query

JUDGED_GRADES = (0, 1, 2, 3)


@dataclass(frozen=True)
class Judgment:
    query: str
    id: str
    grade: int


@dataclass(frozen=True)
class Assessment:
    query: str
    kind: str
    grades: dict[str, int]


class JudgmentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS judgments ("
                "request BLOB PRIMARY KEY, query TEXT NOT NULL, id TEXT NOT NULL, "
                "grade INTEGER NOT NULL) WITHOUT ROWID"
            )

    def load(self) -> dict[bytes, Judgment]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows: list[tuple[bytes, str, str, int]] = connection.execute(
                "SELECT request, query, id, grade FROM judgments"
            ).fetchall()
        return {request: Judgment(query, id_, grade) for request, query, id_, grade in rows}

    def store(self, request: bytes, judgment: Judgment) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO judgments VALUES (?, ?, ?, ?)",
                (request, judgment.query, judgment.id, judgment.grade),
            )


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
