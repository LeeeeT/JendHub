import asyncio

import httpx2
import pytest

from jend import openrouter


def test_api_key_rejects_a_carriage_return_without_showing_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(openrouter.KEY_VARIABLE, "sk-or-secret\r")

    with pytest.raises(openrouter.InvalidKey) as error:
        openrouter.api_key()

    assert "secret" not in str(error.value)


def test_api_key_accepts_a_clean_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openrouter.KEY_VARIABLE, "sk-or-v1-abc123")

    assert openrouter.api_key() == "sk-or-v1-abc123"


def test_api_key_requires_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(openrouter.KEY_VARIABLE, raising=False)

    with pytest.raises(openrouter.InvalidKey):
        openrouter.api_key()


def test_post_does_not_retry_a_local_protocol_error() -> None:
    calls: list[httpx2.Request] = []

    def reject(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise httpx2.LocalProtocolError("illegal header value")

    async def send() -> None:
        async with httpx2.AsyncClient(
            base_url="https://example.test", transport=httpx2.MockTransport(reject)
        ) as client:
            await openrouter.post(client, "/embeddings", {})

    with pytest.raises(httpx2.LocalProtocolError):
        asyncio.run(send())
    assert len(calls) == 1
