from collections import defaultdict

from jendhub.evaluate import Pipeline

RRF_K = 60
DEPTH = 100


class ReciprocalRankFusion:
    def __init__(self, pipelines: list[Pipeline]) -> None:
        self.pipelines = pipelines

    def search(self, query: str, limit: int) -> list[str]:
        scores: defaultdict[str, float] = defaultdict(float)
        for pipeline in self.pipelines:
            for rank, id_ in enumerate(pipeline.search(query, max(limit, DEPTH)), start=1):
                scores[id_] += 1 / (RRF_K + rank)
        return sorted(scores, key=lambda id_: -scores[id_])[:limit]
