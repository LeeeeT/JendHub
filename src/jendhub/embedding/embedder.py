from typing import Protocol

from jendhub.embedding.model import Vectors


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> Vectors: ...
