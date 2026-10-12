from collections import defaultdict

from jendhub.tokens import tokens

MINIMUM_OVERLAP = 0.5


def overlap(query: set[str], name: set[str]) -> float:
    return len(query & name) / len(query | name)


class NameMatch:
    def __init__(self, documents: list[tuple[str, str]]) -> None:
        self.ids = [id_ for id_, _ in documents]
        self.positions = {id_: position for position, id_ in enumerate(self.ids)}
        self.names = [set(tokens(name)) for _, name in documents]
        self.index: defaultdict[str, list[int]] = defaultdict(list)
        for document, name in enumerate(self.names):
            for token in name:
                self.index[token].append(document)

    def search(self, query: str, limit: int) -> list[str]:
        wanted = set(tokens(query))
        candidates = sorted({d for token in wanted for d in self.index.get(token, [])})
        scores = {d: overlap(wanted, self.names[d]) for d in candidates}
        matches = [d for d in candidates if scores[d] >= MINIMUM_OVERLAP]
        ranked = sorted(matches, key=lambda document: -scores[document])
        return [self.ids[document] for document in ranked[:limit]]

    def scores(self, query: str, ids: list[str]) -> list[float]:
        wanted = set(tokens(query))
        return [overlap(wanted, self.names[self.positions[id_]]) for id_ in ids]
