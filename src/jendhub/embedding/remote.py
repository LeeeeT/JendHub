import itertools
import logging

import numpy as np
from prometheus_client import Counter

from jendhub.embedding.model import DIMENSIONS, MODEL, PROVIDER, Vectors, empty
from jendhub.json import Json
from jendhub.remote.openrouter import OpenRouter, OpenRouterError, Usage, for_each

BATCH = 64
CONCURRENCY = 8

TOKENS = Counter("jendhub_embedding_tokens", "The input tokens sent to the embedding model.")
COST = Counter("jendhub_embedding_cost_dollars", "The cost of the embedding requests.")

logger = logging.getLogger(__name__)


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
                    raise OpenRouterError(
                        f"OpenRouter returned an invalid vector: {error}"
                    ) from error
                indexes.add(index)
            case _:
                raise OpenRouterError("OpenRouter returned an invalid embedding item")
    return vectors


def parse_response(body: Json, count: int) -> Vectors:
    match body:
        case {"data": list() as items} if len(items) == count:
            return response_vectors(items, count)
        case _:
            raise OpenRouterError(f"OpenRouter did not return {count} embeddings")


def request(texts: list[str]) -> dict[str, Json]:
    return {
        "model": MODEL,
        "input": list[Json](texts),
        "encoding_format": "float",
        "provider": {"only": [PROVIDER], "allow_fallbacks": False},
    }


class RemoteEmbedder:
    def __init__(self, openrouter: OpenRouter) -> None:
        self.openrouter = openrouter

    async def embed(self, texts: list[str]) -> Vectors:
        unique = list(dict.fromkeys(texts))
        if not unique:
            return empty()
        logger.info("embedding %d texts with %s", len(unique), MODEL)
        found: dict[str, Vectors] = {}
        usage = Usage()

        async def embed_batch(batch: tuple[str, ...]) -> None:
            body = await self.openrouter.post("embeddings", request(list(batch)))
            vectors = parse_response(body, len(batch))
            found.update(zip(batch, vectors, strict=True))
            usage.add(body)

        batches = list(itertools.batched(unique, BATCH, strict=False))
        await for_each(batches, CONCURRENCY, embed_batch)
        TOKENS.inc(usage.prompt_tokens)
        COST.inc(usage.cost)
        logger.info("embedded %d texts: %s", len(unique), usage.describe())
        return np.stack([found[text] for text in texts])
