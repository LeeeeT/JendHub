import asyncio
import itertools
import logging

import httpx

from jendhub.base import fetch_base, latest_tag
from jendhub.hub import Listing, Package, Rejection, fetch_package, new_listings
from jendhub.mirror import Mirror

CONCURRENCY = 8

logger = logging.getLogger(__name__)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"user-agent": "jendhub"},
        timeout=30,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(
            retries=3,
            limits=httpx.Limits(max_connections=CONCURRENCY),
        ),
    )


async def sync_base(client: httpx.AsyncClient, mirror: Mirror) -> None:
    tag = await latest_tag(client)
    if tag == mirror.stored_base_tag():
        logger.info("base.bend is current at %s", tag)
        return
    mirror.store_base(await fetch_base(client, tag))
    logger.info("stored base.bend %s", tag)


async def resolve(client: httpx.AsyncClient, entry: Listing | Rejection) -> Package | Rejection:
    match entry:
        case Listing():
            return await fetch_package(client, entry)
        case Rejection():
            return entry


async def sync_hub(client: httpx.AsyncClient, mirror: Mirror) -> None:
    entries = await new_listings(client, mirror.hashes() | mirror.rejected_hashes())
    logger.info("found %d new packages", len(entries))
    for batch in itertools.batched(reversed(entries), CONCURRENCY, strict=False):
        for result in await asyncio.gather(*(resolve(client, entry) for entry in batch)):
            match result:
                case Package():
                    mirror.store_package(result)
                    logger.info("stored %s", result.listing.hash)
                case Rejection():
                    mirror.reject(result)
                    logger.info("rejected %s: %s", result.hash, result.reason)


async def sync(mirror: Mirror) -> None:
    async with http_client() as http:
        await sync_base(http, mirror)
        await sync_hub(http, mirror)
