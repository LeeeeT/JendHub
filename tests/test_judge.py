import json

import httpx
import pytest
import respx

from jendhub.bench import Answer, Query
from jendhub.definitions import BASE, Definition
from jendhub.hub import Json
from jendhub.judge import (
    MODEL,
    NEIGHBOR_LIMIT,
    PROVIDER,
    Judge,
    Pair,
    current_judgments,
    pair,
    parse_grade,
    roots,
    user_message,
)
from jendhub.judgments import Judgment
from jendhub.openrouter import API, OpenRouterError
from jendhub.uses import UseGraph

ENDPOINT = f"{API}/chat/completions"
MAP = Query("map a list", "phrase", [Answer("List.map", 3)])
TEXT = "def List.map(f, xs): ..."


def definition(name: str) -> Definition:
    return Definition(name, "def", name, BASE, f"def {name}: ...", f"def {name}:", 0, 1)


LIST_MAP = Definition("List.map", "def", "List.map", BASE, TEXT, TEXT, 0, 1)
PAIR = Pair(MAP, LIST_MAP, (), (), ())
OTHER = Pair(MAP, definition("Nat.add"), (), (), ())


def completion(content: str, cost: float = 0.001) -> Json:
    return {"choices": [{"message": {"content": content}}], "usage": {"cost": cost}}


def grade_by_id(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content)["messages"][1]["content"]
    grade = 3 if "List.map" in message else 0
    return httpx.Response(200, json=completion(json.dumps({"grade": grade})))


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def test_user_message_shows_the_query_the_definition_and_its_uses() -> None:
    double = definition("List.double")
    message = user_message(Pair(MAP, LIST_MAP, (double,), (), (double,)))
    assert message == (
        f"Query: map a list\n\nDefinition ID: List.map\n\n{TEXT}\n\n"
        "Uses (0):\n\nUsed by (1):\n\nID: List.double\ndef List.double:\n\n"
        "Reached from (1):\n\nID: List.double\ndef List.double:"
    )


def test_roots_are_the_users_without_users_that_reach_a_definition() -> None:
    users = {
        "step": frozenset({"middle", "entry"}),
        "middle": frozenset({"top", "loop"}),
        "loop": frozenset({"middle"}),
    }
    assert roots("step", users) == ["entry", "top"]
    assert roots("top", users) == []


def test_user_message_shows_a_limited_number_of_uses() -> None:
    users = tuple(definition(f"user{index:02}") for index in range(NEIGHBOR_LIMIT + 2))
    message = user_message(Pair(MAP, LIST_MAP, users, (), ()))
    assert f"Used by ({NEIGHBOR_LIMIT + 2}):" in message
    assert f"ID: user{NEIGHBOR_LIMIT - 1:02}\n" in message
    assert f"ID: user{NEIGHBOR_LIMIT:02}\n" not in message
    assert "And 2 more." in message


def test_pair_takes_the_uses_from_the_graph_in_id_order() -> None:
    definitions = {d.id: d for d in map(definition, ["List.map", "b", "a", "c"])}
    graph = UseGraph({"List.map": frozenset({"b", "a"})}, {"List.map": frozenset({"c"})}, {})
    found = pair(MAP, definitions["List.map"], graph, definitions)
    assert found.users == (definitions["a"], definitions["b"])
    assert found.uses == (definitions["c"],)
    assert found.roots == (definitions["a"], definitions["b"])
    assert pair(MAP, definitions["c"], graph, definitions).users == ()


def test_request_pins_the_model_and_the_provider() -> None:
    request = Judge("prompt").request(PAIR)
    assert request["model"] == MODEL
    assert request["provider"] == {"only": [PROVIDER], "allow_fallbacks": False}
    assert request["messages"] == [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": user_message(PAIR)},
    ]


def test_the_message_length_counts_the_prompt_and_the_user_message() -> None:
    assert Judge("prompt").message_length(PAIR) == len("prompt") + len(user_message(PAIR))


def test_the_key_changes_with_the_prompt_and_the_message() -> None:
    judge = Judge("prompt a")
    assert judge.key(PAIR) == Judge("prompt a").key(PAIR)
    assert judge.key(PAIR) != Judge("prompt b").key(PAIR)
    assert judge.key(PAIR) != judge.key(Pair(MAP, LIST_MAP, (definition("List.double"),), (), ()))


def test_current_judgments_split_the_stored_pairs_into_current_and_stale() -> None:
    definitions = [LIST_MAP, definition("Nat.add"), definition("List.double")]
    by_id = {d.id: d for d in definitions}
    graph = UseGraph({"List.map": frozenset({"List.double"})}, {}, {})
    judge = Judge("prompt")
    current = pair(MAP, LIST_MAP, graph, by_id)
    stored = {
        judge.key(current): Judgment("map a list", "List.map", 3),
        judge.key(Pair(MAP, by_id["Nat.add"], (), (), ())): Judgment("map a list", "Nat.add", 1),
        Judge("old").key(Pair(MAP, by_id["List.double"], (), (), ())): Judgment(
            "map a list", "List.double", 2
        ),
        b"unknown query": Judgment("sort a list", "List.map", 2),
        b"unknown definition": Judgment("map a list", "List.sort", 2),
    }
    judgments, stale = current_judgments(stored, [MAP], definitions, judge, graph)
    assert judgments == [
        Judgment("map a list", "List.map", 3),
        Judgment("map a list", "Nat.add", 1),
    ]
    assert stale == {("map a list", "List.double"): pair(MAP, by_id["List.double"], graph, by_id)}


def test_parse_grade_reads_the_grade() -> None:
    assert parse_grade(completion('{"grade": 2}')) == 2


@pytest.mark.parametrize("content", ['{"grade": 4}', '{"score": 2}', "two"])
def test_parse_grade_refuses_an_invalid_answer(content: str) -> None:
    with pytest.raises(OpenRouterError):
        parse_grade(completion(content))


def test_judge_records_a_grade_for_each_pair(router: respx.MockRouter) -> None:
    router.post(ENDPOINT).mock(side_effect=grade_by_id)
    recorded: dict[str, int] = {}
    usage = Judge("prompt").judge(
        [PAIR, OTHER], lambda pair, grade: recorded.update({pair.definition.id: grade})
    )
    assert recorded == {"List.map": 3, "Nat.add": 0}
    assert usage.requests == 2
    assert usage.cost == pytest.approx(0.002)


def test_judge_of_no_pairs_sends_no_request(router: respx.MockRouter) -> None:
    route = router.post(ENDPOINT)
    assert Judge("prompt").judge([], lambda _pair, _grade: None).requests == 0
    assert route.call_count == 0
