import json
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from jendhub import embeddings, openrouter
from jendhub.embeddings import DIMENSIONS, MODEL, PROVIDER, OpenRouterEmbedder
from jendhub.openrouter import API, OpenRouterError

ENDPOINT = f"{API}/embeddings"


def unit(axis: int) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[axis] = 1.0
    return vector


AXES = {"List.map": 0, "List.fold": 1, "Nat.add": 2}


def embed_by_axis(request: httpx.Request) -> httpx.Response:
    texts: list[str] = json.loads(request.content)["input"]
    items = [{"index": index, "embedding": unit(AXES[text])} for index, text in enumerate(texts)]
    return httpx.Response(200, json={"data": items[::-1]})


@pytest.fixture
def embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OpenRouterEmbedder:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return OpenRouterEmbedder(tmp_path / "embeddings.sqlite")


def test_embed_returns_the_vectors_in_input_order(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    vectors = embedder.embed(["Nat.add", "List.map"])
    assert vectors.shape == (2, DIMENSIONS)
    assert vectors[0][2] == 1.0
    assert vectors[1][0] == 1.0


def test_embed_pins_the_model_and_the_provider(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    embedder.embed(["List.map"])
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["provider"] == {"only": [PROVIDER], "allow_fallbacks": False}
    assert request.headers["authorization"] == "Bearer test-key"


def test_embed_sends_each_text_once(router: respx.MockRouter, embedder: OpenRouterEmbedder) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    vectors = embedder.embed(["List.map", "List.map"])
    assert json.loads(route.calls.last.request.content)["input"] == ["List.map"]
    assert np.array_equal(vectors[0], vectors[1])


def test_embed_splits_the_texts_into_batches(
    router: respx.MockRouter, embedder: OpenRouterEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embeddings, "BATCH", 2)
    sizes: list[int] = []

    def record(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["input"]))
        return embed_by_axis(request)

    router.post(ENDPOINT).mock(side_effect=record)
    embedder.embed(["List.map", "List.fold", "Nat.add"])
    assert sorted(sizes) == [1, 2]


def test_embed_reads_cached_vectors_without_a_request(
    router: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    first = OpenRouterEmbedder(tmp_path / "embeddings.sqlite").embed(["List.map", "Nat.add"])
    monkeypatch.delenv("OPENROUTER_API_KEY")
    second = OpenRouterEmbedder(tmp_path / "embeddings.sqlite").embed(["Nat.add", "List.map"])
    assert route.call_count == 1
    assert np.array_equal(first, second[::-1])


def test_embed_requests_only_the_missing_texts(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    route = router.post(ENDPOINT).mock(side_effect=embed_by_axis)
    embedder.embed(["List.map"])
    embedder.embed(["List.map", "Nat.add"])
    assert json.loads(route.calls.last.request.content)["input"] == ["Nat.add"]


def test_embed_of_no_texts_gives_an_empty_matrix(embedder: OpenRouterEmbedder) -> None:
    assert embedder.embed([]).shape == (0, DIMENSIONS)


def test_embed_requires_the_api_key_for_missing_texts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
        OpenRouterEmbedder(tmp_path / "embeddings.sqlite").embed(["List.map"])


def test_embed_retries_a_rate_limited_request(
    router: respx.MockRouter, embedder: OpenRouterEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(openrouter, "RETRY_DELAYS", (0,))
    statuses: list[int] = []

    def limit_once(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(429, json={}) if not statuses else embed_by_axis(request)
        statuses.append(response.status_code)
        return response

    router.post(ENDPOINT).mock(side_effect=limit_once)
    assert embedder.embed(["List.map"])[0][0] == 1.0
    assert statuses == [429, 200]


def test_embed_reports_the_error_message(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    router.post(ENDPOINT).respond(400, json={"error": {"message": "bad input"}})
    with pytest.raises(OpenRouterError, match="bad input"):
        embedder.embed(["List.map"])


def test_embed_rejects_a_vector_with_the_wrong_dimensions(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    router.post(ENDPOINT).respond(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
    with pytest.raises(OpenRouterError):
        embedder.embed(["List.map"])


def test_embed_rejects_a_repeated_index(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    items = [{"index": 0, "embedding": unit(0)}, {"index": 0, "embedding": unit(1)}]
    router.post(ENDPOINT).respond(200, json={"data": items})
    with pytest.raises(OpenRouterError):
        embedder.embed(["List.map", "Nat.add"])


def test_embed_rejects_a_response_that_is_not_json(
    router: respx.MockRouter, embedder: OpenRouterEmbedder
) -> None:
    router.post(ENDPOINT).respond(403, text="forbidden")
    with pytest.raises(OpenRouterError, match="403"):
        embedder.embed(["List.map"])
