from jendhub.enrich import Enrichment
from jendhub.roles import ApiRole

ROLES = ApiRole(
    {
        "List.map": Enrichment("List.map", "Maps.", ["map"], "api"),
        "List.map.go": Enrichment("List.map.go", "Loops.", ["loop"], "helper"),
    }
)


def test_api_role_scores_api_definitions_one_and_helpers_zero() -> None:
    assert ROLES.scores(["q", "p"], [["List.map", "List.map.go"], ["List.map.go"]]) == [
        [1.0, 0.0],
        [0.0],
    ]
