from collections import defaultdict

from jendhub.lab.pipeline import QueryBatch
from jendhub.search.names import overlap
from jendhub.search.tokens import tokens

MINIMUM_OVERLAP = 0.5


class NameMatch:
    def __init__(self, documents: list[tuple[str, str]]) -> None:
        self.ids = [id_ for id_, _ in documents]
        self.positions = {id_: position for position, id_ in enumerate(self.ids)}
        self.names = [set(tokens(name)) for _, name in documents]
        self.index: defaultdict[str, list[int]] = defaultdict(list)
        for document, name in enumerate(self.names):
            for token in name:
                self.index[token].append(document)

    def ranking(self, query: str, limit: int) -> list[str]:
        wanted = set(tokens(query))
        candidates = sorted({d for token in wanted for d in self.index.get(token, [])})
        scores = {d: overlap(wanted, self.names[d]) for d in candidates}
        matches = [d for d in candidates if scores[d] >= MINIMUM_OVERLAP]
        ranked = sorted(matches, key=lambda document: -scores[document])
        return [self.ids[document] for document in ranked[:limit]]

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset()

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]:
        return [self.ranking(query, limit) for query in batch.texts]

    def overlaps(self, query: str, ids: list[str]) -> list[float]:
        wanted = set(tokens(query))
        return [overlap(wanted, self.names[self.positions[id_]]) for id_ in ids]

    def scores(self, batch: QueryBatch, ids: list[list[str]]) -> list[list[float]]:
        return [
            self.overlaps(query, query_ids)
            for query, query_ids in zip(batch.texts, ids, strict=True)
        ]
