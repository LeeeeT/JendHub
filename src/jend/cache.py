import json
import sqlite3
from pathlib import Path


class Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "create table if not exists results"
            " (index_id text, query text, ranking text, primary key (index_id, query))"
        )

    def get(self, index_id: str, query: str) -> list[tuple[str, float]] | None:
        row = self.connection.execute(
            "select ranking from results where index_id = ? and query = ?", (index_id, query)
        ).fetchone()
        if row is None:
            return None
        return [(key, probability) for key, probability in json.loads(row[0])]

    def put(self, index_id: str, query: str, ranking: list[tuple[str, float]]) -> None:
        with self.connection:
            self.connection.execute(
                "insert or replace into results values (?, ?, ?)",
                (index_id, query, json.dumps(ranking)),
            )

    def close(self) -> None:
        self.connection.close()
