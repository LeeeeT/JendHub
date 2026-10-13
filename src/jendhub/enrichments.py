import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from jendhub.enrich import Enrichment, Window, prompt
from jendhub.errors import JendHubError


class EnrichError(JendHubError):
    pass


def window_key(window: Window) -> bytes:
    return hashlib.sha256(json.dumps(prompt(window), sort_keys=True).encode()).digest()


class EnrichmentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS enrichments ("
                "window BLOB NOT NULL, id TEXT NOT NULL, summary TEXT NOT NULL, "
                "queries TEXT NOT NULL, role TEXT NOT NULL, "
                "PRIMARY KEY (window, id)) WITHOUT ROWID"
            )

    def load(self, windows: list[Window]) -> tuple[dict[str, Enrichment], list[Window]]:
        found: dict[str, Enrichment] = {}
        missing: list[Window] = []
        with closing(sqlite3.connect(self.path)) as connection:
            for window in windows:
                rows: list[tuple[str, str, str, str]] = connection.execute(
                    "SELECT id, summary, queries, role FROM enrichments WHERE window = ?",
                    (window_key(window),),
                ).fetchall()
                if {id_ for id_, *_ in rows} != {target.id for target in window.targets}:
                    missing.append(window)
                    continue
                for id_, summary, queries, role in rows:
                    found[id_] = Enrichment(id_, summary, json.loads(queries), role)
        return found, missing

    def store(self, window: Window, enrichments: list[Enrichment]) -> None:
        key = window_key(window)
        rows = [
            (key, item.id, item.summary, json.dumps(item.queries, ensure_ascii=False), item.role)
            for item in enrichments
        ]
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany(
                "INSERT OR REPLACE INTO enrichments VALUES (?, ?, ?, ?, ?)", rows
            )
