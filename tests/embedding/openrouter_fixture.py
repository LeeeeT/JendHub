from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import SecretStr

from jendhub.remote.http import http_client
from jendhub.remote.openrouter import OpenRouter, Patience

KEY = "test-key"
QUICK = Patience(timeout=5, retry_delays=(0,))


@asynccontextmanager
async def fake_openrouter() -> AsyncGenerator[OpenRouter]:
    async with http_client() as client:
        yield OpenRouter(client, SecretStr(KEY), QUICK)
