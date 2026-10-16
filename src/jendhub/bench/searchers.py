from typing import Protocol

from jendhub.embedding.embedder import Embedder
from jendhub.lab.pipeline import Pipeline, prepare
from jendhub.search.engine import SearchEngine


class Searcher(Protocol):
    async def search(self, queries: list[str], limit: int) -> list[list[str]]: ...


class LabSearcher:
    def __init__(self, pipeline: Pipeline, embedder: Embedder) -> None:
        self.pipeline = pipeline
        self.embedder = embedder

    async def search(self, queries: list[str], limit: int) -> list[list[str]]:
        batch = await prepare(queries, self.pipeline.instructions, self.embedder)
        return self.pipeline.search(batch, limit)


class EngineSearcher:
    def __init__(self, engine: SearchEngine) -> None:
        self.engine = engine

    async def search(self, queries: list[str], limit: int) -> list[list[str]]:
        return [[hit.id for hit in hits] for hits in await self.engine.search(queries, limit)]
