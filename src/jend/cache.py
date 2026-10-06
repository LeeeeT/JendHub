import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.float32]


class EmbeddingCache:
    def __init__(self, path: Path, model: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "create table if not exists embeddings"
            " (model text, text text, vector blob, primary key (model, text))"
        )

    def get(self, text: str) -> Vector | None:
        row = self.connection.execute(
            "select vector from embeddings where model = ? and text = ?", (self.model, text)
        ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row[0], dtype=np.float32).copy()

    def put(self, texts: Sequence[str], vectors: Sequence[Vector]) -> None:
        with self.connection:
            self.connection.executemany(
                "insert or replace into embeddings values (?, ?, ?)",
                [
                    (self.model, text, np.asarray(vector, dtype=np.float32).tobytes())
                    for text, vector in zip(texts, vectors, strict=True)
                ],
            )

    def close(self) -> None:
        self.connection.close()
