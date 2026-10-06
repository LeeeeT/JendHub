from typesafe_sdk import AsyncTypeSafeClient

from jend import openrouter

OPENROUTER_BASE_URL = "https://openrouter.ai/api"
MODEL = "jev-1.13"
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000


def connect(timeout: float | None = None) -> AsyncTypeSafeClient:
    return AsyncTypeSafeClient(
        api_key=openrouter.api_key(),
        base_url=OPENROUTER_BASE_URL,
        model=MODEL,
        timeout=timeout,
    )
