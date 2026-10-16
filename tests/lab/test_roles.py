from jendhub.enrich.enricher import Enrichment
from jendhub.lab.roles import ApiRole
from tests.lab.batches import batch

ROLES = ApiRole(
    {
        "List.map": Enrichment("List.map", "Maps.", ["map"], "api"),
        "List.map.go": Enrichment("List.map.go", "Loops.", ["loop"], "helper"),
    }
)


def test_api_role_scores_api_definitions_one_and_helpers_zero() -> None:
    assert ROLES.scores(batch(["q", "p"]), [["List.map", "List.map.go"], ["List.map.go"]]) == [
        [1.0, 0.0],
        [0.0],
    ]


def test_api_role_scores_a_definition_without_enrichment_zero() -> None:
    assert ROLES.scores(batch(["q"]), [["List.fold"]]) == [[0.0]]
