import asyncio
import logging
import os

import httpx2

BASE_URL = "https://openrouter.ai/api/v1"
RETRIES = 4


def connect(timeout: float = 180.0) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
        timeout=timeout,
    )


_log = logging.getLogger(__name__)


async def post(client: httpx2.AsyncClient, path: str, body: dict[str, object]) -> bytes:
    for attempt in range(RETRIES + 1):
        try:
            response = await client.post(path, json=body)
        except httpx2.TransportError as error:
            if attempt == RETRIES:
                raise
            reason = f"{type(error).__name__}: {error}"
        else:
            if response.status_code < 500 and response.status_code != 429:
                response.raise_for_status()
                return response.content
            if attempt == RETRIES:
                response.raise_for_status()
            reason = f"HTTP {response.status_code}: {response.text[:200]}"
        _log.warning("OpenRouter %s attempt %d failed, retrying: %s", path, attempt + 1, reason)
        await asyncio.sleep(2.0**attempt)
    raise AssertionError("unreachable")
