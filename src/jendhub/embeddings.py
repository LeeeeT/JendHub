import asyncio
import hashlib
import itertools
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import numpy as np

from jendhub.dense import Vectors
from jendhub.errors import JendHubError
from jendhub.http import CONCURRENCY, http_client
from jendhub.hub import Json

ENDPOINT = "https://openrouter.ai/api/v1/embeddings"
MODEL = "qwen/qwen3-embedding-8b"
PROVIDER = "nebius"
DIMENSIONS = 4096
BATCH = 64
TIMEOUT = 120
RETRY_DELAYS = (1, 2, 4, 8, 16, 32)
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

logger = logging.getLogger(__name__)


class EmbeddingError(JendHubError):
    pass


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise EmbeddingError("OPENROUTER_API_KEY is not set: run with uv run --env-file .env")
    return key


def digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def response_vectors(items: list[Json], count: int) -> Vectors:
    vectors = np.zeros((count, DIMENSIONS), dtype=np.float32)
    indexes: set[int] = set()
    for item in items:
        match item:
            case {"index": int() as index, "embedding": list() as values} if (
                0 <= index < count and index not in indexes and len(values) == DIMENSIONS
            ):
                try:
                    vectors[index] = np.array(values, dtype=np.float32)
                except (TypeError, ValueError) as error:
                    raise EmbeddingError(
                        f"OpenRouter returned an invalid vector: {error}"
                    ) from error
                indexes.add(index)
            case _:
                raise EmbeddingError("OpenRouter returned an invalid embedding item")
    return vectors


def parse_response(body: Json, count: int) -> Vectors:
    match body:
        case {"data": list() as items} if len(items) == count:
            return response_vectors(items, count)
        case {"error": {"message": str() as message}}:
            raise EmbeddingError(f"OpenRouter: {message}")
        case _:
            raise EmbeddingError(f"OpenRouter did not return {count} embeddings")


async def post(client: httpx.AsyncClient, key: str, texts: list[str]) -> httpx.Response:
    request = {
        "model": MODEL,
        "input": texts,
        "encoding_format": "float",
        "provider": {"only": [PROVIDER], "allow_fallbacks": False},
    }
    headers = {"authorization": f"Bearer {key}"}
    for delay in RETRY_DELAYS:
        response = await client.post(ENDPOINT, json=request, headers=headers, timeout=TIMEOUT)
        if response.status_code not in RETRY_STATUSES:
            return response
        logger.info("OpenRouter returned %d: retry in %d s", response.status_code, delay)
        await asyncio.sleep(delay)
    return await client.post(ENDPOINT, json=request, headers=headers, timeout=TIMEOUT)


async def request_vectors(client: httpx.AsyncClient, key: str, texts: list[str]) -> Vectors:
    response = await post(client, key, texts)
    try:
        body: Json = response.json()
    except json.JSONDecodeError as error:
        raise EmbeddingError(f"OpenRouter returned status {response.status_code}") from error
    return parse_response(body, len(texts))


class EmbeddingCache:
    def __init__(self, path: Path, model: str) -> None:
        self.path = path
        self.model = model
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS embeddings ("
                "model TEXT NOT NULL, digest BLOB NOT NULL, vector BLOB NOT NULL, "
                "PRIMARY KEY (model, digest)) WITHOUT ROWID"
            )

    def load(self, digests: list[bytes]) -> dict[bytes, Vectors]:
        found: dict[bytes, Vectors] = {}
        with closing(sqlite3.connect(self.path)) as connection:
            for key in set(digests):
                row: tuple[bytes] | None = connection.execute(
                    "SELECT vector FROM embeddings WHERE model = ? AND digest = ?",
                    (self.model, key),
                ).fetchone()
                if row is not None:
                    found[key] = np.frombuffer(row[0], dtype=np.float32)
        return found

    def store(self, texts: list[str], vectors: Vectors) -> None:
        rows = [
            (self.model, digest(text), vector.astype(np.float32).tobytes())
            for text, vector in zip(texts, vectors, strict=True)
        ]
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)", rows)


class OpenRouterEmbedder:
    def __init__(self, cache_path: Path) -> None:
        self.cache = EmbeddingCache(cache_path, f"{PROVIDER}/{MODEL}")

    def embed(self, texts: list[str]) -> Vectors:
        digests = [digest(text) for text in texts]
        found = self.cache.load(digests)
        missing = list(dict.fromkeys(text for text in texts if digest(text) not in found))
        if missing:
            logger.info("embedding %d texts with %s", len(missing), MODEL)
            asyncio.run(self.fetch(missing))
            found = self.cache.load(digests)
        return np.array([found[key] for key in digests], dtype=np.float32).reshape(-1, DIMENSIONS)

    async def fetch(self, texts: list[str]) -> None:
        key = api_key()
        batches = list(itertools.batched(texts, BATCH, strict=False))
        async with http_client() as client:
            for group in itertools.batched(batches, CONCURRENCY, strict=False):
                results = await asyncio.gather(
                    *(request_vectors(client, key, list(batch)) for batch in group)
                )
                for batch, vectors in zip(group, results, strict=True):
                    self.cache.store(list(batch), vectors)
