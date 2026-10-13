import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from jendhub.errors import JendHubError
from jendhub.http import http_client
from jendhub.hub import Json

API = "https://openrouter.ai/api/v1"
TIMEOUT = 120
RETRY_DELAYS = (1, 2, 4, 8, 16, 32)
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

logger = logging.getLogger(__name__)


class OpenRouterError(JendHubError):
    pass


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set: run with uv run --env-file .env")
    return key


async def send(
    client: httpx.AsyncClient, key: str, path: str, request: dict[str, Json]
) -> httpx.Response:
    url = f"{API}/{path}"
    headers = {"authorization": f"Bearer {key}"}
    for delay in RETRY_DELAYS:
        response = await client.post(url, json=request, headers=headers, timeout=TIMEOUT)
        if response.status_code not in RETRY_STATUSES:
            return response
        logger.info("OpenRouter returned %d: retry in %d s", response.status_code, delay)
        await asyncio.sleep(delay)
    return await client.post(url, json=request, headers=headers, timeout=TIMEOUT)


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0

    def add(self, body: Json) -> None:
        self.requests += 1
        match body:
            case {"usage": dict() as usage}:
                self.prompt_tokens += count(usage.get("prompt_tokens"))
                self.completion_tokens += count(usage.get("completion_tokens"))
                match usage.get("completion_tokens_details"):
                    case {"reasoning_tokens": int() as reasoning}:
                        self.reasoning_tokens += reasoning
                    case _:
                        pass
                match usage.get("cost"):
                    case int() | float() as cost:
                        self.cost += cost
                    case _:
                        pass
            case _:
                pass

    def describe(self) -> str:
        return (
            f"{self.requests} requests, {self.prompt_tokens} prompt tokens,"
            f" {self.completion_tokens} completion tokens"
            f" ({self.reasoning_tokens} reasoning), ${self.cost:.4f}"
        )


def count(value: Json) -> int:
    return value if isinstance(value, int) else 0


def message_content(body: Json) -> str:
    match body:
        case {"choices": [{"message": {"content": str() as content}}, *_]}:
            return content
        case _:
            raise OpenRouterError("OpenRouter returned no message")


async def for_each[T](
    items: list[T],
    concurrency: int,
    handle: Callable[[httpx.AsyncClient, str, T], Awaitable[None]],
) -> None:
    key = api_key()
    pending = iter(items)
    async with http_client(concurrency) as client:

        async def work() -> None:
            for item in pending:
                await handle(client, key, item)

        await asyncio.gather(*(work() for _ in range(concurrency)))


async def post(client: httpx.AsyncClient, key: str, path: str, request: dict[str, Json]) -> Json:
    response = await send(client, key, path, request)
    try:
        body: Json = response.json()
    except json.JSONDecodeError as error:
        raise OpenRouterError(f"OpenRouter returned status {response.status_code}") from error
    match body:
        case {"error": {"message": str() as message}}:
            raise OpenRouterError(f"OpenRouter: {message}")
        case _ if response.is_error:
            raise OpenRouterError(f"OpenRouter returned status {response.status_code}")
        case _:
            return body
