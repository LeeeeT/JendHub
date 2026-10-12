from collections import defaultdict

from jendhub.evaluate import Pipeline

RRF_K = 60
DEPTH = 100


def fuse(rankings: list[list[str]], limit: int) -> list[str]:
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id_ in enumerate(ranking, start=1):
            scores[id_] += 1 / (RRF_K + rank)
    return sorted(scores, key=lambda id_: -scores[id_])[:limit]


class ReciprocalRankFusion:
    def __init__(self, pipelines: list[Pipeline]) -> None:
        self.pipelines = pipelines

    def search(self, queries: list[str], limit: int) -> list[list[str]]:
        depth = max(limit, DEPTH)
        results = [pipeline.search(queries, depth) for pipeline in self.pipelines]
        return [fuse([result[index] for result in results], limit) for index in range(len(queries))]
