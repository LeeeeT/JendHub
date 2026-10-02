import asyncio
from pathlib import Path, PurePosixPath

import httpx2
from pydantic import BaseModel, ConfigDict

BASE_URL = "https://hub.bend-lang.com"
PAGE_SIZE = 100
DOWNLOADS_IN_FLIGHT = 8


class Listing(BaseModel):
    model_config = ConfigDict(frozen=True)

    hash: str
    name: str | None
    version: str | None
    desc: str
    ts: int
    hot: float
    files: dict[str, int]

    def bend_files(self) -> list[str]:
        return sorted(path for path in self.files if path.endswith(".bend"))


class _Page(BaseModel):
    total: int
    packages: list[Listing]


async def fetch_listings(client: httpx2.AsyncClient) -> list[Listing]:
    listings: dict[str, Listing] = {}
    offset = 0
    while True:
        response = await client.get(
            "/packages.json", params={"sort": "new", "limit": PAGE_SIZE, "after": offset}
        )
        response.raise_for_status()
        page = _Page.model_validate_json(response.content)
        for listing in page.packages:
            listings.setdefault(listing.hash, listing)
        offset += len(page.packages)
        if not page.packages or offset >= page.total:
            return list(listings.values())


def cached_path(cache: Path, package_hash: str, path: str) -> Path:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe file path in package {package_hash}: {path!r}")
    return cache / package_hash / relative


async def download(client: httpx2.AsyncClient, listings: list[Listing], cache: Path) -> int:
    missing = [
        (listing.hash, path)
        for listing in listings
        for path in listing.bend_files()
        if not cached_path(cache, listing.hash, path).exists()
    ]
    gate = asyncio.Semaphore(DOWNLOADS_IN_FLIGHT)

    async def fetch(package_hash: str, path: str) -> None:
        async with gate:
            response = await client.get(f"/{package_hash}/{path}")
        response.raise_for_status()
        target = cached_path(cache, package_hash, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(response.content)
        partial.replace(target)

    await asyncio.gather(*(fetch(package_hash, path) for package_hash, path in missing))
    return len(missing)


def connect() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(base_url=BASE_URL, timeout=60.0, follow_redirects=True)
