import asyncio
import itertools
import logging
from dataclasses import dataclass

import httpx

from jendhub.hub.api import (
    Listing,
    Package,
    PackageRelease,
    Rejection,
    fetch_package,
    listings,
    name_releases,
)
from jendhub.hub.base import fetch_base, latest_tag
from jendhub.hub.store import PackageStore
from jendhub.remote.http import CONCURRENCY, http_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Plan:
    stored_base_tag: str | None
    latest_base_tag: str
    entries: list[Listing | Rejection]
    names: set[str]

    @property
    def base_is_current(self) -> bool:
        return self.stored_base_tag == self.latest_base_tag


async def make_plan(client: httpx.AsyncClient, store: PackageStore) -> Plan:
    tag = await latest_tag(client)
    known = await store.known_hashes()
    listed = [entry async for entry in listings(client, "new")]
    entries = [entry for entry in reversed(listed) if entry.hash not in known]
    names = await store.names()
    names.update(
        entry.release.name
        for entry in listed
        if isinstance(entry, Listing) and entry.release is not None
    )
    names.update(await asyncio.to_thread(store.library.imported_names))
    return Plan(await store.latest_base_tag(), tag, entries, names)


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
    summary = (
        f"names to refresh: {len(plan.names)}, new packages: {len(plan.entries)},"
        f" to fetch: {fetches}, to reject: {rejections}"
    )
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

    await refresh_names(client, store, plan.names)


async def store_named_release(
    client: httpx.AsyncClient,
    store: PackageStore,
    release: PackageRelease,
    stored: set[str],
) -> None:
    if release.hash not in stored:
        package = await fetch_package(client, Listing(release.hash, None))
        if isinstance(package, Rejection):
            await store.reject(package)
            return
        await store.store_package(package)
        stored.add(release.hash)
    await store.store_release(release)


async def refresh_names(client: httpx.AsyncClient, store: PackageStore, names: set[str]) -> None:
    stored = await store.stored_hashes()
    rejected = await store.rejected_hashes()
    refreshed: set[str] = set()
    pending = names | await asyncio.to_thread(store.library.imported_names)
    while pending:
        for batch in itertools.batched(sorted(pending), CONCURRENCY, strict=False):
            records = await asyncio.gather(*(name_releases(client, name) for name in batch))
            for releases in records:
                for release in releases:
                    if release.hash not in rejected:
                        await store_named_release(client, store, release, stored)
            refreshed.update(batch)
        pending = await asyncio.to_thread(store.library.imported_names) - refreshed


async def plan(store: PackageStore) -> Plan:
    async with http_client() as http:
        return await make_plan(http, store)


async def sync(store: PackageStore) -> Plan:
    async with http_client() as http:
        found = await make_plan(http, store)
        await apply_plan(http, store, found)
        return found
