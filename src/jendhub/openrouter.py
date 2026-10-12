import asyncio
import json
import logging
import os

import httpx

from jendhub.errors import JendHubError
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
