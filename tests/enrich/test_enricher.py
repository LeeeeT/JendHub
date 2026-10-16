import json

import httpx
import pytest
import respx

from jendhub.corpus.definitions import BASE, SourceFile
from jendhub.enrich import enricher
from jendhub.enrich.enricher import (
    MODEL,
    PROMPT,
    PROVIDERS,
    Enrichment,
    InvalidAnswerError,
    parse_enrichments,
    request,
    user_message,
)
from jendhub.enrich.windows import Window, windows
from jendhub.json import Json
from jendhub.remote.openrouter import API
from tests.embedding.openrouter_fixture import fake_openrouter
from tests.enrich.fixture import source

ENDPOINT = f"{API}/chat/completions"


def entry(name: str, role: str = "api") -> dict[str, Json]:
    return {"name": name, "summary": f"Does {name}.", "queries": [f"{name} query"], "role": role}


def answer(entries: list[dict[str, Json]]) -> str:
    return json.dumps({"definitions": list[Json](entries)})


def completion(entries: list[dict[str, Json]]) -> Json:
    return {"choices": [{"message": {"content": answer(entries)}}], "usage": {"cost": 0.01}}


def enrich_all(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content)["messages"][1]["content"]
    names = message.split("Write the metadata for these definitions:\n")[1].splitlines()
    return httpx.Response(200, json=completion([entry(name) for name in names]))


def test_the_prompt_explains_forward_declarations() -> None:
    assert "forward declaration" in PROMPT


def window_of(names: list[str]) -> Window:
    return windows(SourceFile(BASE, "\n".join(f"def {n}(x):\n  x\n" for n in names)))[0]


def test_parse_enrichments_returns_one_entry_for_each_target_in_order() -> None:
    window = window_of(["a", "b"])
    content = answer([entry("b", "helper"), entry("a")])
    assert parse_enrichments(content, window) == [
        Enrichment("a", "Does a.", ["a query"], "api"),
        Enrichment("b", "Does b.", ["b query"], "helper"),
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [entry("a")],
        [entry("a"), entry("a"), entry("b")],
        [entry("a"), entry("b"), entry("c")],
        [entry("a"), entry("b", "test")],
        [entry("a"), {**entry("b"), "queries": []}],
        [entry("a"), {**entry("b"), "summary": " "}],
    ],
)
def test_parse_enrichments_refuses_an_invalid_answer(entries: list[dict[str, Json]]) -> None:
    with pytest.raises(InvalidAnswerError):
        parse_enrichments(answer(entries), window_of(["a", "b"]))


def test_parse_enrichments_refuses_invalid_json() -> None:
    with pytest.raises(InvalidAnswerError):
        parse_enrichments('{"definitions": [', window_of(["a"]))


def test_request_turns_reasoning_off_and_routes_to_the_providers_in_order() -> None:
    body = request(window_of(["a"]))
    assert body["model"] == MODEL
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {
        "order": list(PROVIDERS),
        "only": list(PROVIDERS),
        "allow_fallbacks": True,
        "require_parameters": True,
    }


def test_request_restricts_the_names_to_the_targets() -> None:
    match request(window_of(["a", "b"]))["response_format"]:
        case {"json_schema": {"schema": {"properties": {"definitions": {"items": items}}}}}:
            match items:
                case {"properties": {"name": {"enum": names}}}:
                    assert names == ["a", "b"]
                case _:
                    pytest.fail("no name enum")
        case _:
            pytest.fail("no schema")


async def refuse_record(_window: Window, _found: list[Enrichment]) -> None:
    pytest.fail("recorded an answer")


async def refuse_reject(_window: Window, reason: str) -> None:
    pytest.fail(reason)


@pytest.mark.usefixtures("small")
async def test_enrich_records_the_enrichments_of_each_window(router: respx.MockRouter) -> None:
    router.post(ENDPOINT).mock(side_effect=enrich_all)
    recorded: list[str] = []

    async def record(_window: Window, found: list[Enrichment]) -> None:
        recorded.extend(item.id for item in found)

    async with fake_openrouter() as openrouter:
        outcome = await enricher.enrich(
            openrouter, windows(SourceFile(BASE, source(3))), record, refuse_reject
        )
    assert sorted(recorded) == ["f0", "f1", "f2"]
    assert outcome.usage.requests == 2
    assert outcome.rejected == []
    assert outcome.errors == []


async def test_enrich_rejects_an_invalid_answer_after_one_request(
    router: respx.MockRouter,
) -> None:
    router.post(ENDPOINT).respond(200, json=completion([entry("f0"), entry("f0")]))
    [window] = windows(SourceFile(BASE, source(1)))
    rejected: list[Window] = []

    async def reject(window: Window, _reason: str) -> None:
        rejected.append(window)

    async with fake_openrouter() as openrouter:
        outcome = await enricher.enrich(openrouter, [window], refuse_record, reject)
    assert rejected == [window]
    assert outcome.usage.requests == 1
    assert len(outcome.rejected) == 1
    assert outcome.rejected[0].startswith("Base line 1:")
    assert outcome.errors == []


async def test_enrich_reports_a_failed_request_without_a_rejection(
    router: respx.MockRouter,
) -> None:
    router.post(ENDPOINT).respond(401, json={"error": {"message": "invalid key"}})
    async with fake_openrouter() as openrouter:
        outcome = await enricher.enrich(
            openrouter, windows(SourceFile(BASE, source(1))), refuse_record, refuse_reject
        )
    assert outcome.rejected == []
    assert len(outcome.errors) == 1
    assert outcome.errors[0].startswith("Base line 1:")


@pytest.mark.usefixtures("small")
def test_user_message_shows_the_window_and_the_target_names() -> None:
    window = windows(SourceFile(BASE, source(3)))[1]
    message = user_message(window)
    assert message.startswith("===== Base, lines 1-11 =====\n# Step 0.")
    assert message.endswith("Write the metadata for these definitions:\nf2")
