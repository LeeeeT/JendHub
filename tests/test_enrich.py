import json

import httpx
import pytest
import respx

from jendhub import enrich
from jendhub.definitions import BASE, SourceFile
from jendhub.enrich import (
    MODEL,
    PROMPT,
    PROVIDER,
    Enrichment,
    Window,
    parse_enrichments,
    request,
    windows,
)
from jendhub.hub import Json
from jendhub.openrouter import API, OpenRouterError

ENDPOINT = f"{API}/chat/completions"


def source(count: int) -> str:
    return "\n".join(
        f"# Step {index}.\ndef f{index}(n: Nat) -> Nat:\n  n\n" for index in range(count)
    )


def entry(name: str, role: str = "api") -> dict[str, Json]:
    return {"name": name, "summary": f"Does {name}.", "queries": [f"{name} query"], "role": role}


def completion(entries: list[dict[str, Json]]) -> Json:
    content = json.dumps({"definitions": list[Json](entries)})
    return {"choices": [{"message": {"content": content}}], "usage": {"cost": 0.01}}


def enrich_all(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content)["messages"][1]["content"]
    names = message.split("Write the metadata for these definitions:\n")[1].splitlines()
    return httpx.Response(200, json=completion([entry(name) for name in names]))


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enrich, "BEFORE", 2)
    monkeypatch.setattr(enrich, "TARGETS", 2)
    monkeypatch.setattr(enrich, "AFTER", 1)


@pytest.mark.usefixtures("small")
def test_windows_split_the_targets_and_add_context_on_both_sides() -> None:
    found = windows(SourceFile(BASE, source(7)))
    assert [[target.name for target in window.targets] for window in found] == [
        ["f0", "f1"],
        ["f2", "f3"],
        ["f4", "f5"],
        ["f6"],
    ]
    middle = found[1]
    assert middle.lines[0] == "# Step 0."
    assert middle.lines[-1] == "  n"
    assert "def f4(n: Nat) -> Nat:" in middle.lines
    assert "def f5(n: Nat) -> Nat:" not in middle.lines


@pytest.mark.usefixtures("small")
def test_windows_drop_context_beyond_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enrich, "CONTEXT_LIMIT", 40)
    window = windows(SourceFile(BASE, source(7)))[1]
    assert window.lines[0] == "# Step 1."
    assert "def f4(n: Nat) -> Nat:" in window.lines


def test_windows_keep_the_lines_between_definitions() -> None:
    text = "law f:\n  Nat\n\ndef f(n):\n  n\n\n# Section\n\ndef g(n: Nat) -> Nat:\n  f(n)\n"
    [window] = windows(SourceFile(BASE, text))
    assert window.lines == text.rstrip().splitlines()


@pytest.mark.usefixtures("small")
def test_user_message_shows_the_window_and_the_target_names() -> None:
    window = windows(SourceFile(BASE, source(3)))[1]
    message = window.user_message()
    assert message.startswith("===== Base, lines 1-11 =====\n# Step 0.")
    assert message.endswith("Write the metadata for these definitions:\nf2")


def test_the_prompt_explains_forward_declarations() -> None:
    assert "forward declaration" in PROMPT


def window_of(names: list[str]) -> Window:
    return windows(SourceFile(BASE, "\n".join(f"def {n}(x):\n  x\n" for n in names)))[0]


def test_parse_enrichments_returns_one_entry_for_each_target_in_order() -> None:
    window = window_of(["a", "b"])
    body = completion([entry("b", "helper"), entry("a")])
    assert parse_enrichments(body, window) == [
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
    with pytest.raises(OpenRouterError):
        parse_enrichments(completion(entries), window_of(["a", "b"]))


def test_request_turns_reasoning_off_and_pins_the_provider() -> None:
    body = request(window_of(["a"]))
    assert body["model"] == MODEL
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"only": [PROVIDER], "allow_fallbacks": False}


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


@pytest.mark.usefixtures("small")
def test_enrich_records_the_enrichments_of_each_window(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    router.post(ENDPOINT).mock(side_effect=enrich_all)
    recorded: list[str] = []
    usage = enrich.enrich(
        windows(SourceFile(BASE, source(3))),
        lambda found: recorded.extend(item.id for item in found),
    )
    assert sorted(recorded) == ["f0", "f1", "f2"]
    assert usage.requests == 2
