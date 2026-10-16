from jendhub.enrich.enricher import Enrichment
from jendhub.lab.pipeline import QueryBatch

API_ROLE = "api"


class ApiRole:
    def __init__(self, enrichments: dict[str, Enrichment]) -> None:
        self.enrichments = enrichments

    @property
    def instructions(self) -> frozenset[str]:
        return frozenset()

    def scores(self, _batch: QueryBatch, ids: list[list[str]]) -> list[list[float]]:
        return [[float(self.is_api(id_)) for id_ in query_ids] for query_ids in ids]

    def is_api(self, id_: str) -> bool:
        enrichment = self.enrichments.get(id_)
        return enrichment is not None and enrichment.role == API_ROLE
