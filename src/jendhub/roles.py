from jendhub.enrich import Enrichment

API_ROLE = "api"


class ApiRole:
    def __init__(self, enrichments: dict[str, Enrichment]) -> None:
        self.enrichments = enrichments

    def scores(self, _queries: list[str], ids: list[list[str]]) -> list[list[float]]:
        return [
            [float(self.enrichments[id_].role == API_ROLE) for id_ in query_ids]
            for query_ids in ids
        ]
