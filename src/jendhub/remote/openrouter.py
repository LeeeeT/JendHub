import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from jendhub.errors import JendHubError
from jendhub.json import Json
from jendhub.remote.http import CONCURRENCY, http_client
from jendhub.settings import OpenRouterSettings

API = "https://openrouter.ai/api/v1"
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Patience:
    timeout: float
    retry_delays: tuple[float, ...]


BATCH = Patience(timeout=120, retry_delays=(1, 2, 4, 8, 16, 32))
INTERACTIVE = Patience(timeout=10, retry_delays=(0.5,))

logger = logging.getLogger(__name__)


class OpenRouterError(JendHubError):
    pass


class OpenRouter:
    def __init__(self, client: httpx.AsyncClient, key: SecretStr, patience: Patience) -> None:
        self.client = client
        self.key = key
        self.patience = patience

    async def send(self, path: str, request: dict[str, Json]) -> httpx.Response:
        url = f"{API}/{path}"
        headers = {"authorization": f"Bearer {self.key.get_secret_value()}"}
        timeout = self.patience.timeout
        for delay in self.patience.retry_delays:
            response = await self.client.post(url, json=request, headers=headers, timeout=timeout)
            if response.status_code not in RETRY_STATUSES:
                return response
            logger.info("OpenRouter returned %d: retry in %s s", response.status_code, delay)
            await asyncio.sleep(delay)
        return await self.client.post(url, json=request, headers=headers, timeout=timeout)

    async def post(self, path: str, request: dict[str, Json]) -> Json:
        response = await self.send(path, request)
        try:
            body: Json = response.json()
        except json.JSONDecodeError as error:
            raise OpenRouterError(f"OpenRouter returned status {response.status_code}") from error
        match body:
            case {"error": {"message": str() as message, "metadata": {"raw": str() as raw}}}:
                raise OpenRouterError(f"OpenRouter: {message}: {raw[:500]}")
            case {"error": {"message": str() as message}}:
                raise OpenRouterError(f"OpenRouter: {message}")
            case _ if response.is_error:
                raise OpenRouterError(f"OpenRouter returned status {response.status_code}")
            case _:
                return body


@asynccontextmanager
async def open_openrouter(
    settings: OpenRouterSettings, patience: Patience = BATCH, connections: int = CONCURRENCY
) -> AsyncGenerator[OpenRouter]:
    async with http_client(connections) as client:
        yield OpenRouter(client, settings.openrouter_api_key, patience)


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
    items: list[T], concurrency: int, handle: Callable[[T], Awaitable[None]]
) -> None:
    pending = iter(items)

    async def work() -> None:
        for item in pending:
            await handle(item)

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(concurrency):
                group.create_task(work())
    except ExceptionGroup as failures:
        raise failures.exceptions[0] from failures
