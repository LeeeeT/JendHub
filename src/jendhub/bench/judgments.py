from dataclasses import dataclass

from jendhub.bench.queries import Query
from jendhub.db.pool import Pool

JUDGED_GRADES = (0, 1, 2, 3)


@dataclass(frozen=True)
class Judgment:
    query: str
    id: str
    grade: int


@dataclass(frozen=True)
class Assessment:
    text: str
    kind: str
    grades: dict[str, int]


class JudgmentStore:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def load(self) -> dict[bytes, Judgment]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT request, query, id, grade FROM judgments")
            rows = await cursor.fetchall()
        return {request: Judgment(query, id_, grade) for request, query, id_, grade in rows}

    async def store(self, request: bytes, judgment: Judgment) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO judgments (request, query, id, grade) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (request) DO UPDATE"
                " SET query = excluded.query, id = excluded.id, grade = excluded.grade",
                (request, judgment.query, judgment.id, judgment.grade),
            )


def assessments(queries: list[Query], judgments: list[Judgment]) -> list[Assessment]:
    judged: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        judged.setdefault(judgment.query, {})[judgment.id] = judgment.grade
    return [
        Assessment(
            query.text,
            query.kind,
            judged.get(query.text, {}) | {answer.id: answer.grade for answer in query.answers},
        )
        for query in queries
    ]
