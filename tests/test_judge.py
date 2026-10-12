import json

import httpx
import pytest
import respx

from jendhub.bench import Answer, Query
from jendhub.definitions import BASE, Definition
from jendhub.hub import Json
from jendhub.judge import MODEL, PROVIDER, Judge, Pair, parse_grade, user_message
from jendhub.openrouter import API, OpenRouterError

ENDPOINT = f"{API}/chat/completions"
MAP = Query("map a list", "phrase", [Answer("List.map", 3)])
TEXT = "def List.map(f, xs): ..."
PAIR = Pair(MAP, Definition("List.map", "def", "List.map", BASE, TEXT, TEXT))
OTHER = Pair(MAP, Definition("Nat.add", "def", "Nat.add", BASE, "def Nat.add", "def Nat.add"))


def completion(content: str, cost: float = 0.001) -> Json:
    return {"choices": [{"message": {"content": content}}], "usage": {"cost": cost}}


def grade_by_id(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content)["messages"][1]["content"]
    grade = 3 if "List.map" in message else 0
    return httpx.Response(200, json=completion(json.dumps({"grade": grade})))


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def test_user_message_shows_the_query_and_the_definition() -> None:
    assert user_message(PAIR) == (
        f"Query: map a list\nQuery kind: phrase\n\nDefinition ID: List.map\n\n{TEXT}"
    )


def test_request_pins_the_model_and_the_provider() -> None:
    request = Judge("prompt").request(PAIR)
    assert request["model"] == MODEL
    assert request["provider"] == {"only": [PROVIDER], "allow_fallbacks": False}
    assert request["messages"] == [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": user_message(PAIR)},
    ]


def test_the_judge_name_changes_with_the_prompt() -> None:
    assert Judge("prompt a").name != Judge("prompt b").name


def test_parse_grade_reads_the_grade_and_the_cost() -> None:
    assert parse_grade(completion('{"grade": 2}', 0.5)) == (2, 0.5)


@pytest.mark.parametrize("content", ['{"grade": 4}', '{"score": 2}', "two"])
def test_parse_grade_refuses_an_invalid_answer(content: str) -> None:
    with pytest.raises(OpenRouterError):
        parse_grade(completion(content))


def test_judge_records_a_grade_for_each_pair(router: respx.MockRouter) -> None:
    router.post(ENDPOINT).mock(side_effect=grade_by_id)
    recorded: dict[str, int] = {}
    cost = Judge("prompt").judge(
        [PAIR, OTHER], lambda pair, grade: recorded.update({pair.definition.id: grade})
    )
    assert recorded == {"List.map": 3, "Nat.add": 0}
    assert cost == pytest.approx(0.002)


def test_judge_of_no_pairs_sends_no_request(router: respx.MockRouter) -> None:
    route = router.post(ENDPOINT)
    assert Judge("prompt").judge([], lambda _pair, _grade: None) == 0.0
    assert route.call_count == 0
