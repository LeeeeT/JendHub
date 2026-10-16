import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def every(interval: float, stop: asyncio.Event, step: Callable[[], Awaitable[None]]) -> None:
    while not stop.is_set():
        try:
            await step()
        except Exception:
            logger.exception("a background step failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)
