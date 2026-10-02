import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import httpx2
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from jend import openrouter

MODEL = "qwen/qwen3-embedding-8b"
DIMENSIONS = 1024
BATCH = 128
REQUESTS_IN_FLIGHT = 8
QUERY_TASK = "Given a description of what a programmer needs, find Bend definitions that do it"

Vectors = npt.NDArray[np.float32]


class _Datum(BaseModel):
    index: int
    embedding: list[float]


class _Usage(BaseModel):
    cost: float


class _Response(BaseModel):
    data: list[_Datum]
    usage: _Usage


def query_text(query: str) -> str:
    return f"Instruct: {QUERY_TASK}\nQuery: {query}"


async def embed(client: httpx2.AsyncClient, texts: Sequence[str]) -> tuple[Vectors, float]:
    gate = asyncio.Semaphore(REQUESTS_IN_FLIGHT)

    async def batch(start: int) -> tuple[Vectors, float]:
        async with gate:
            body: dict[str, object] = {
                "model": MODEL,
                "input": list(texts[start : start + BATCH]),
                "dimensions": DIMENSIONS,
            }
            response = _Response.model_validate_json(
                await openrouter.post(client, "/embeddings", body)
            )
        ordered = sorted(response.data, key=lambda datum: datum.index)
        return np.array(
            [datum.embedding for datum in ordered], dtype=np.float32
        ), response.usage.cost

    parts = await asyncio.gather(*(batch(start) for start in range(0, len(texts), BATCH)))
    if not parts:
        return np.zeros((0, DIMENSIONS), dtype=np.float32), 0.0
    vectors = np.concatenate([part for part, _ in parts])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors, sum(cost for _, cost in parts)


class Store:
    def __init__(self, directory: Path) -> None:
        self.keys_path = directory / "vector_keys.json"
        self.vectors_path = directory / "vectors.npy"

    def load(self) -> tuple[list[str], Vectors]:
        if not self.keys_path.exists():
            return [], np.zeros((0, DIMENSIONS), dtype=np.float32)
        keys: list[str] = json.loads(self.keys_path.read_text())
        vectors: Vectors = np.load(self.vectors_path)
        return keys, vectors

    def save(self, keys: list[str], vectors: Vectors) -> None:
        self.keys_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.vectors_path, vectors)
        self.keys_path.write_text(json.dumps(keys))
