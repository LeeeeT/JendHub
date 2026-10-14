from jendhub.enrich import Enrichment

API_ROLE = "api"


class ApiRole:
    def __init__(self, enrichments: dict[str, Enrichment]) -> None:
        self.enrichments = enrichments

    def scores(self, _queries: list[str], ids: list[list[str]]) -> list[list[float]]:
        return [[float(self.is_api(id_)) for id_ in query_ids] for query_ids in ids]

    def is_api(self, id_: str) -> bool:
        enrichment = self.enrichments.get(id_)
        return enrichment is not None and enrichment.role == API_ROLE
