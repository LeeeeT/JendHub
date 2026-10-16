from jendhub.lab.pipeline import QueryBatch


def batch(queries: list[str]) -> QueryBatch:
    return QueryBatch(queries, {})


class FixedPipeline:
    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self.rankings = rankings

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset()

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]:
        return [self.rankings.get(query, [])[:limit] for query in batch.texts]


class FixedScorer:
    def __init__(self, scores: dict[str, float]) -> None:
        self.table = scores

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset()

    def scores(self, batch: QueryBatch, ids: list[list[str]]) -> list[list[float]]:
        return [[self.table.get(id_, 0.0) for id_ in query_ids] for query_ids in ids]
