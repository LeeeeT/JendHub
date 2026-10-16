from jendhub.lab.pipeline import Pipeline, QueryBatch, Scorer

DEPTH = 50


def max_normalized(scores: list[float]) -> list[float]:
    top = max(scores, default=0.0)
    return [score / top if top > 0 else 0.0 for score in scores]


class MaxNormalized:
    def __init__(self, scorer: Scorer) -> None:
        self.scorer = scorer

    @property
    def instructions(self) -> frozenset[str]:
        return self.scorer.instructions

    def scores(self, batch: QueryBatch, ids: list[list[str]]) -> list[list[float]]:
        return [max_normalized(scores) for scores in self.scorer.scores(batch, ids)]


def reranked(ids: list[str], totals: list[float], limit: int) -> list[str]:
    order = sorted(range(len(ids)), key=lambda index: -totals[index])
    return [ids[index] for index in order[:limit]]


class LinearRerank:
    def __init__(self, candidates: Pipeline, features: list[tuple[float, Scorer]]) -> None:
        self.candidates = candidates
        self.features = features

    @property
    def instructions(self) -> frozenset[str]:
        found = self.candidates.instructions
        return found.union(*(scorer.instructions for _, scorer in self.features))

    def search(self, batch: QueryBatch, limit: int) -> list[list[str]]:
        candidates = self.candidates.search(batch, max(limit, DEPTH))
        totals = [[0.0] * len(ids) for ids in candidates]
        for weight, scorer in self.features:
            for total, scores in zip(totals, scorer.scores(batch, candidates), strict=True):
                for index, score in enumerate(scores):
                    total[index] += weight * score
        return [reranked(ids, total, limit) for ids, total in zip(candidates, totals, strict=True)]
