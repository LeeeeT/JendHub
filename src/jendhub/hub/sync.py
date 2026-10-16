import asyncio
import itertools
import logging
from dataclasses import dataclass

import httpx

from jendhub.hub.api import Listing, Package, Rejection, fetch_package, new_listings
from jendhub.hub.base import fetch_base, latest_tag
from jendhub.hub.store import PackageStore
from jendhub.remote.http import CONCURRENCY, http_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Plan:
    stored_base_tag: str | None
    latest_base_tag: str
    entries: list[Listing | Rejection]

    @property
    def base_is_current(self) -> bool:
        return self.stored_base_tag == self.latest_base_tag


async def make_plan(client: httpx.AsyncClient, store: PackageStore) -> Plan:
    tag = await latest_tag(client)
    entries = await new_listings(client, await store.known_hashes())
    return Plan(await store.latest_base_tag(), tag, list(reversed(entries)))


def describe_entry(entry: Listing | Rejection) -> str:
    match entry:
        case Listing(hash_, None):
            return f"fetch {hash_}"
        case Listing(hash_, release):
            return f"fetch {hash_} {release}"
        case Rejection(hash_, reason):
            return f"reject {hash_}: {reason}"


def describe_plan(plan: Plan) -> str:
    if plan.base_is_current:
        base = f"base.bend is current at {plan.latest_base_tag}"
    else:
        base = f"fetch base.bend {plan.latest_base_tag} (stored: {plan.stored_base_tag})"
    fetches = sum(isinstance(entry, Listing) for entry in plan.entries)
    rejections = len(plan.entries) - fetches
    summary = f"new packages: {len(plan.entries)}, to fetch: {fetches}, to reject: {rejections}"
    return "\n".join([base, *map(describe_entry, plan.entries), summary])


async def resolve(client: httpx.AsyncClient, entry: Listing | Rejection) -> Package | Rejection:
    match entry:
        case Listing():
            return await fetch_package(client, entry)
        case Rejection():
            return entry


async def apply_plan(client: httpx.AsyncClient, store: PackageStore, plan: Plan) -> None:
    if plan.base_is_current:
        logger.info("base.bend is current at %s", plan.latest_base_tag)
    else:
        await store.store_base(await fetch_base(client, plan.latest_base_tag))
        logger.info("stored base.bend %s", plan.latest_base_tag)
    logger.info("found %d new packages", len(plan.entries))
    for batch in itertools.batched(plan.entries, CONCURRENCY, strict=False):
        for result in await asyncio.gather(*(resolve(client, entry) for entry in batch)):
            match result:
                case Package():
                    await store.store_package(result)
                    logger.info("stored %s", result.listing.hash)
                case Rejection():
                    await store.reject(result)
                    logger.info("rejected %s: %s", result.hash, result.reason)


async def plan(store: PackageStore) -> Plan:
    async with http_client() as http:
        return await make_plan(http, store)


async def sync(store: PackageStore) -> Plan:
    async with http_client() as http:
        found = await make_plan(http, store)
        await apply_plan(http, store, found)
        return found
