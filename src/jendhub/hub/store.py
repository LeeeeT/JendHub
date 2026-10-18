import asyncio

from jendhub.db.pool import Pool
from jendhub.hub.api import Listing, Package, Rejection, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library
from jendhub.settings import LibrarySettings, load_settings


class PackageStore:
    def __init__(self, pool: Pool, library: Library) -> None:
        self.pool = pool
        self.library = library

    async def known_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash FROM packages UNION ALL SELECT hash FROM rejected_packages"
            )
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def stored_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT hash FROM packages")
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def rejected_hashes(self) -> set[str]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT hash FROM rejected_packages")
            return {hash_ for (hash_,) in await cursor.fetchall()}

    async def package(self, hash_: str) -> Package:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT name, version FROM packages WHERE hash = %s", (hash_,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(hash_)
        release = None if row[0] is None else Release(row[0], row[1])
        files = await asyncio.to_thread(self.library.read_package, hash_)
        return Package(Listing(hash_, release), files)

    async def latest_base_tag(self) -> str | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT tag FROM base_releases ORDER BY stored_at DESC, tag DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return None if row is None else row[0]

    async def store_base(self, base: Base) -> None:
        await asyncio.to_thread(self.library.store_base, base)
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO base_releases (tag) VALUES (%s) ON CONFLICT DO NOTHING", (base.tag,)
            )

    async def store_package(self, package: Package) -> None:
        await asyncio.to_thread(self.library.store_package, package)
        release = package.listing.release
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO packages (hash, name, version) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    package.listing.hash,
                    None if release is None else release.name,
                    None if release is None else release.version,
                ),
            )

    async def reject(self, rejection: Rejection) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO rejected_packages (hash, reason) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (rejection.hash, rejection.reason),
            )

    async def releases(self, hashes: list[str]) -> dict[str, Release | None]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT hash, name, version FROM packages WHERE hash = ANY(%s)", (hashes,)
            )
            rows = await cursor.fetchall()
        return {
            hash_: None if name is None else Release(name, version) for hash_, name, version in rows
        }


def package_store(pool: Pool) -> PackageStore:
    return PackageStore(pool, Library(load_settings(LibrarySettings).library_path))
