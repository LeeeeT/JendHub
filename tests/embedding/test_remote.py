import json
from collections.abc import AsyncGenerator

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from jendhub.embedding import remote
from jendhub.embedding.model import DIMENSIONS, MODEL, PROVIDER
from jendhub.embedding.remote import RemoteEmbedder
from jendhub.remote.openrouter import API, OpenRouterError
from tests.embedding.openrouter_fixture import KEY, fake_openrouter

ENDPOINT = f"{API}/embeddings"
AXES = {"List.map": 0, "List.fold": 1, "Nat.add": 2}


def unit(axis: int) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[axis] = 1.0
    return vector


def embed_by_axis(request: httpx.Request) -> httpx.Response:
    texts: list[str] = json.loads(request.content)["input"]
    items = [{"index": index, "embedding": unit(AXES[text])} for index, text in enumerate(texts)]
    usage = {"prompt_tokens": len(texts), "cost": 0.001}
    return httpx.Response(200, json={"data": items[::-1], "usage": usage})


@pytest.fixture
async def embedder() -> AsyncGenerator[RemoteEmbedder]:
    async with fake_openrouter() as client:
        yield RemoteEmbedder(client)


async def test_embed_returns_the_vectors_in_input_order(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    vectors = await embedder.embed(["Nat.add", "List.map"])
    assert vectors.shape == (2, DIMENSIONS)
    assert vectors[0][2] == 1.0
    assert vectors[1][0] == 1.0


async def test_embed_pins_the_model_and_the_provider(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    await embedder.embed(["List.map"])
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["provider"] == {"only": [PROVIDER], "allow_fallbacks": False}
    assert request.headers["authorization"] == f"Bearer {KEY}"


async def test_embed_sends_each_text_once(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    vectors = await embedder.embed(["List.map", "List.map"])
    assert json.loads(route.calls.last.request.content)["input"] == ["List.map"]
    assert (vectors[0] == vectors[1]).all()


async def test_embed_splits_the_texts_into_batches(
    router: respx.MockRouter, embedder: RemoteEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "BATCH", 2)
    sizes: list[int] = []

    def record(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return embed_by_axis(request)

    router.post(ENDPOINT).mock(side_effect=record)
    await embedder.embed(["List.map", "List.fold", "Nat.add"])
    assert sorted(sizes) == [1, 2]


async def test_embed_of_no_texts_gives_an_empty_matrix(embedder: RemoteEmbedder) -> None:
    assert (await embedder.embed([])).shape == (0, DIMENSIONS)


async def test_embed_retries_a_rate_limited_request(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    statuses: list[int] = []

    def limit_once(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(429, json={}) if not statuses else embed_by_axis(request)
        statuses.append(response.status_code)
        return response

    router.post(ENDPOINT).mock(side_effect=limit_once)
    assert (await embedder.embed(["List.map"]))[0][0] == 1.0
    assert statuses == [429, 200]


async def test_embed_retries_a_request_that_gets_no_answer(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    attempts: list[httpx.Request] = []

    def time_out_once(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("no answer", request=request)
        return embed_by_axis(request)

    router.post(ENDPOINT).mock(side_effect=time_out_once)
    assert (await embedder.embed(["List.map"]))[0][0] == 1.0
    assert len(attempts) == 2


async def test_embed_reports_a_request_that_never_gets_an_answer(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(OpenRouterError, match="OpenRouter did not answer: ConnectError"):
        await embedder.embed(["List.map"])


async def test_embed_reports_the_error_message(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).respond(400, json={"error": {"message": "bad input"}})
    with pytest.raises(OpenRouterError, match="bad input"):
        await embedder.embed(["List.map"])


async def test_embed_reports_the_raw_message_of_the_provider(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    error = {"message": "Provider returned error", "metadata": {"raw": "engine overloaded"}}
    router.post(ENDPOINT).respond(429, json={"error": error})
    with pytest.raises(OpenRouterError, match="Provider returned error: engine overloaded"):
        await embedder.embed(["List.map"])


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": 0, "embedding": unit(0)}, {"index": 0, "embedding": unit(1)}],
    ],
)
async def test_embed_rejects_an_invalid_item(
    router: respx.MockRouter, embedder: RemoteEmbedder, data: list[object]
) -> None:
    router.post(ENDPOINT).respond(200, json={"data": data})
    with pytest.raises(OpenRouterError):
        await embedder.embed(["List.map", "Nat.add"][: len(data)])


async def test_embed_rejects_a_response_that_is_not_json(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).respond(403, text="forbidden")
    with pytest.raises(OpenRouterError, match="403"):
        await embedder.embed(["List.map"])


async def test_embed_rejects_a_response_that_is_not_utf8(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).respond(200, content=b"\xff\xfe{")
    with pytest.raises(OpenRouterError, match="200"):
        await embedder.embed(["List.map"])


def tokens() -> float:
    return REGISTRY.get_sample_value("jendhub_embedding_tokens_total") or 0.0


async def test_embed_counts_the_tokens_and_the_cost(
    router: respx.MockRouter, embedder: RemoteEmbedder
) -> None:
    router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    before = tokens()
    await embedder.embed(["List.map", "Nat.add"])
    assert tokens() - before == 2
