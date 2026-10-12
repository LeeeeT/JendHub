from typing import Protocol

from jendhub.evaluate import Pipeline

DEPTH = 50


class Scorer(Protocol):
    def scores(self, query: str, ids: list[str]) -> list[float]: ...


class MaxNormalized:
    def __init__(self, scorer: Scorer) -> None:
        self.scorer = scorer

    def scores(self, query: str, ids: list[str]) -> list[float]:
        raw = self.scorer.scores(query, ids)
        top = max(raw, default=0.0)
        return [score / top if top > 0 else 0.0 for score in raw]


class LinearRerank:
    def __init__(self, candidates: Pipeline, features: list[tuple[float, Scorer]]) -> None:
        self.candidates = candidates
        self.features = features

    def search(self, query: str, limit: int) -> list[str]:
        ids = self.candidates.search(query, max(limit, DEPTH))
        totals = [0.0] * len(ids)
        for weight, scorer in self.features:
            for index, score in enumerate(scorer.scores(query, ids)):
                totals[index] += weight * score
        order = sorted(range(len(ids)), key=lambda index: -totals[index])
        return [ids[index] for index in order[:limit]]
