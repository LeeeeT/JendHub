import asyncio
import hashlib
import re
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Literal

import httpx

from jendhub.errors import JendHubError
from jendhub.json import Json

HUB = "https://hub.bend-lang.com"
PAGE_SIZE = 100

HASH = re.compile(r"0x[0-9a-f]{32}")
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){3}")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


class HubError(JendHubError):
    pass


@dataclass(frozen=True)
class Release:
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @staticmethod
    def parse(text: str) -> Release:
        name, _, version = text.partition("@")
        if not valid_release(name, version):
            raise HubError(f"invalid release {text!r}")
        return Release(name, version)


@dataclass(frozen=True)
class Listing:
    hash: str
    release: Release | None


@dataclass(frozen=True)
class PackageRelease:
    hash: str
    release: Release


@dataclass(frozen=True)
class Package:
    listing: Listing
    files: dict[str, bytes]


@dataclass(frozen=True)
class Rejection:
    hash: str
    reason: str


def valid_release(name: str, version: str) -> bool:
    return bool(NAME.fullmatch(name) and VERSION.fullmatch(version))


def parse_listing(item: Json) -> Listing | Rejection:
    match item:
        case {"hash": str() as hash_} if HASH.fullmatch(hash_):
            pass
        case _:
            raise HubError(f"invalid package listing: {item!r}")
    match item:
        case {"name": None}:
            return Listing(hash_, None)
        case {"name": str() as name, "version": str() as version} if valid_release(name, version):
            return Listing(hash_, Release(name, version))
        case _:
            return Rejection(hash_, "invalid release name or version")


def parse_page(body: Json) -> list[Listing | Rejection]:
    match body:
        case {"packages": list() as items}:
            return [parse_listing(item) for item in items]
        case _:
            raise HubError("invalid package page")


def parse_manifest(manifest: str) -> dict[str, str]:
    digests: dict[str, str] = {}
    for line in manifest.strip().split("\n"):
        digest, _, path = line.partition(" ")
        digests[path] = digest
    return digests


def valid_path(path: str) -> bool:
    return all(
        PATH_SEGMENT.fullmatch(segment) and segment not in {".", ".."}
        for segment in path.split("/")
    )


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def fetch(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    return response.content


async def listings(
    client: httpx.AsyncClient, sort: Literal["new", "hot"]
) -> AsyncGenerator[Listing | Rejection]:
    seen: set[str] = set()
    offset = 0
    while True:
        response = await client.get(
            f"{HUB}/packages.json",
            params={"sort": sort, "limit": PAGE_SIZE, "after": offset},
        )
        response.raise_for_status()
        body: Json = response.json()
        page = parse_page(body)
        for entry in page:
            if entry.hash not in seen:
                seen.add(entry.hash)
                yield entry
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


async def new_listings(client: httpx.AsyncClient, known: set[str]) -> list[Listing | Rejection]:
    found: list[Listing | Rejection] = []
    async with aclosing(listings(client, "new")) as entries:
        async for entry in entries:
            if entry.hash in known:
                break
            found.append(entry)
    return found


async def fetch_package(client: httpx.AsyncClient, listing: Listing) -> Package | Rejection:
    manifest = await fetch(client, f"{HUB}/{listing.hash}/manifest")
    if sha256(manifest)[:32] != listing.hash.removeprefix("0x"):
        raise HubError(f"manifest of {listing.hash} does not match its hash")
    all_digests = parse_manifest(manifest.decode())
    for path in all_digests:
        if not valid_path(path):
            return Rejection(listing.hash, f"invalid file name {path!r}")
    digests = {path: digest for path, digest in all_digests.items() if path.endswith(".bend")}
    contents = await asyncio.gather(
        *(fetch(client, f"{HUB}/{listing.hash}/{path}") for path in digests)
    )
    files = dict(zip(digests, contents, strict=True))
    for path, content in files.items():
        if sha256(content) != digests[path]:
            raise HubError(f"{listing.hash}/{path} does not match its manifest")
    return Package(listing, files)
